#!/usr/bin/env python3
"""List and play Desktop media in an owned Ubuntu desktop service."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import unicodedata

UNIT = 'hermes-demo-media.service'
AUDIO = {'.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aac', '.opus'}
VIDEO = {'.mp4', '.mkv', '.webm', '.mov', '.avi', '.m4v'}


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
        exact = [f for f in files if f['name'].casefold() == target.casefold()]
        files = exact or [f for f in files if normalized(target) in normalized(f['name'])]
    return files, 'not_found' if not files else 'ambiguous' if len(files) > 1 else ''


def status():
    result = subprocess.run(['systemctl', '--user', 'show', UNIT,
                             '-p', 'ActiveState,SubState,MainPID,Result'],
                            capture_output=True, text=True, timeout=5)
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if not values:
        raise RuntimeError('User desktop service manager is unavailable.')
    return {'success': True, 'state': values.get('ActiveState', 'inactive'),
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


def control(action, target=''):
    folder = Path.home() / 'Desktop'
    if action == 'list':
        return {'success': True, 'action': 'listed', 'files': catalog(folder)}
    if action == 'status':
        return status()
    if action == 'stop':
        before = status()
        if before['state'] in ('active', 'activating'):
            subprocess.run(['systemctl', '--user', 'stop', UNIT], check=True, timeout=8)
        return {**status(), 'action': 'stopped'}
    if action not in ('mp3', 'mp4', 'play'):
        raise ValueError('Unsupported media action.')
    files, error = resolve_media(action, folder, target)
    if error:
        return {'success': False, 'error': error, 'files': files}
    command = player_command(action, folder, files[0]['name'])
    # Own only this unit: never stop VLC, browsers, TTS or unrelated players.
    subprocess.run(['systemctl', '--user', 'stop', UNIT], capture_output=True, timeout=8)
    launch = ['systemd-run', '--user', '--collect', '--unit=' + UNIT,
              '--property=Type=exec']
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
