#!/usr/bin/env python3
"""List and play Desktop media in an owned Ubuntu desktop service."""
import argparse
from difflib import SequenceMatcher
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
import unicodedata

UNIT = 'hermes-demo-media.service'
AUDIO = {'.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aac', '.opus'}
VIDEO = {'.mp4', '.mkv', '.webm', '.mov', '.avi', '.m4v'}
# Exact executable identities, never argv substring matches (which could kill
# ASR/FFmpeg decoders, the agent, or the audio server). Dedicated demo device.
PLAYERS = {'ffplay', 'mpv', 'vlc', 'totem', 'celluloid', 'rhythmbox',
           'mplayer', 'parole', 'audacious', 'smplayer', 'mpg123', 'mpg321',
           'paplay', 'pw-play', 'aplay'}


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold() if c.isalnum())


def catalog(folder):
    if not folder.is_dir():
        return []
    return [{'name': p.name, 'kind': 'audio' if p.suffix.lower() in AUDIO else 'video'}
            for p in sorted(folder.iterdir(), key=lambda p: p.name.casefold())
            if not p.name.startswith('.') and not p.is_symlink() and p.is_file()
            and p.suffix.lower() in AUDIO | VIDEO and p.stat().st_size > 0]


def resolve_media(action, folder, target=''):
    files = catalog(folder)
    if action in ('mp3', 'mp4'):
        files = [f for f in files if f['kind'] == ('audio' if action == 'mp3' else 'video')]
    if target:
        if '/' in target or '\\' in target or not normalized(target):
            return [], 'not_found'
        exact = [f for f in files if normalized(target) in
                 (normalized(f['name']), normalized(Path(f['name']).stem))]
        if exact:
            return exact[:1], ''
        def score(item):
            name = normalized(Path(item['name']).stem)
            query = normalized(target)
            ratio = SequenceMatcher(None, query, name).ratio()
            return max(ratio, .9 if query in name or name in query else 0)
        ranked = sorted(files, key=lambda f: (-score(f), f['name'].casefold()))
        files = ranked[:1] if ranked and score(ranked[0]) >= .30 else []
    return files, 'not_found' if not files else 'ambiguous' if len(files) > 1 else ''


def status():
    result = subprocess.run(['systemctl', '--user', 'show', UNIT,
                             '-p', 'ActiveState,SubState,MainPID,Result,ControlGroup,LoadState'],
                            capture_output=True, text=True, timeout=5)
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if not values:
        raise RuntimeError('User desktop service manager is unavailable.')
    group = values.get('ControlGroup', '')
    members = []
    if group:
        for path in (Path('/sys/fs/cgroup') / group.lstrip('/')).rglob('cgroup.procs'):
            try:
                members.extend(path.read_text().split())
            except FileNotFoundError:
                pass
    return {'success': True, 'state': values.get('ActiveState', 'inactive'),
            'members': members,
            'pid': int(values.get('MainPID', '0')), 'result': values.get('Result', '')}


def player_command(kind, folder, target=''):
    if kind not in ('mp3', 'mp4', 'play'):
        raise ValueError('Choose play, mp3 or mp4.')
    files, error = resolve_media(kind, folder, target)
    if error:
        raise RuntimeError('Media file missing or ambiguous: ' + target)
    media = folder / files[0]['name']
    player = shutil.which('ffplay')
    if not player:
        raise RuntimeError('ffplay is missing; install the Ubuntu ffmpeg package.')
    command = [player, '-autoexit', '-loglevel', 'error', '-nostats',
               '-volume', '65', '-window_title', 'Intel Local Media Demo']
    if files[0]['kind'] == 'audio':
        command += ['-nodisp']
    else:
        command += ['-x', '960', '-y', '540']
    return command + [str(media)]


def stopped(current):
    return current['state'] in ('inactive', 'failed') and current['pid'] == 0 and not current['members']


def stop_owned_player():
    # Always send stop: another channel may have restarted playback.
    subprocess.run(['systemctl', '--user', 'stop', '--no-block', UNIT],
                   capture_output=True, timeout=2)
    # A collected/absent unit can return nonzero; verify actual state below.
    deadline = time.monotonic() + .8
    while time.monotonic() < deadline:
        current = status()
        if stopped(current):
            return {**current, 'action': 'stopped'}
        time.sleep(.04)
    subprocess.run(['systemctl', '--user', 'kill', '--signal=SIGKILL', '--kill-whom=all', UNIT],
                   capture_output=True, timeout=2)
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        current = status()
        if stopped(current):
            return {**current, 'action': 'stopped'}
        time.sleep(.04)
    return {'success': False, 'error': 'Media player processes did not stop.', **{'state': current['state']}}


def media_processes():
    found = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            exe = Path(os.readlink(entry / 'exe')).name
            if exe in PLAYERS:
                found.append(int(entry.name))
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    return found


def signal_players(pids, sig):
    for pid in pids:
        try:
            # Bind to the process, then revalidate identity; never kill a reused PID.
            fd = os.pidfd_open(pid)
            try:
                entry = Path('/proc') / str(pid)
                if entry.stat().st_uid == os.getuid() and Path(os.readlink(entry / 'exe')).name in PLAYERS:
                    signal.pidfd_send_signal(fd, sig)
            finally:
                os.close(fd)
        except (FileNotFoundError, ProcessLookupError):
            pass


def stop_player():
    owned = stop_owned_player()
    signal_players(media_processes(), signal.SIGTERM)
    deadline = time.monotonic() + .8
    while media_processes() and time.monotonic() < deadline:
        time.sleep(.04)
    signal_players(media_processes(), signal.SIGKILL)
    deadline = time.monotonic() + .8
    while media_processes() and time.monotonic() < deadline:
        time.sleep(.04)
    remaining = media_processes()
    if remaining or not owned['success']:
        return {'success': False, 'error': 'Media processes did not all stop.', 'remaining_pids': remaining}
    return {'success': True, 'action': 'stopped', 'state': 'inactive'}


def control(action, target=''):
    folder = Path.home() / 'Desktop'
    if action == 'list':
        return {'success': True, 'action': 'listed', 'files': catalog(folder)}
    if action == 'status':
        return status()
    if action == 'stop':
        return stop_player()
    if action not in ('mp3', 'mp4', 'play'):
        raise ValueError('Unsupported media action.')
    files, error = resolve_media(action, folder, target)
    if error:
        return {'success': False, 'error': error, 'files': files}
    command = player_command(action, folder, files[0]['name'])
    # Starting a new file replaces our own player; only explicit stop is broad.
    if not stop_owned_player()['success']:
        raise RuntimeError('Previous media player did not stop.')
    launch = ['systemd-run', '--user', '--collect', '--unit=' + UNIT,
              '--property=Type=exec', '--property=KillMode=control-group',
              '--property=TimeoutStopSec=1s', '--property=SendSIGKILL=yes']
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'XAUTHORITY'):
        if os.environ.get(key):
            launch.append('--setenv=' + key + '=' + os.environ[key])
    result = subprocess.run(launch + command, capture_output=True, text=True, timeout=8)
    if result.returncode:
        raise RuntimeError('Player could not start: ' + result.stderr.strip())
    time.sleep(0.5)
    current = status()
    if current['state'] != 'active':
        raise RuntimeError('Player exited before playback could be confirmed. Check journalctl --user -u ' + UNIT)
    return {**current, 'action': 'started', 'file': command[-1],
            'note': 'Player is running; physical audibility is not verified.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['list', 'play', 'mp3', 'mp4', 'stop', 'status'])
    parser.add_argument('--target', default='')
    args = parser.parse_args()
    try:
        result = control(args.action, args.target)
    except Exception as exc:
        result = {'success': False, 'error': str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
