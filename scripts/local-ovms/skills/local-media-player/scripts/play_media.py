#!/usr/bin/env python3
"""Play fixed local demo assets in an owned Ubuntu desktop service."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

UNIT = 'hermes-demo-media.service'


def status():
    result = subprocess.run(['systemctl', '--user', 'show', UNIT,
                             '-p', 'ActiveState,SubState,MainPID,Result'],
                            capture_output=True, text=True, timeout=5)
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if not values:
        raise RuntimeError('User desktop service manager is unavailable.')
    return {'success': True, 'state': values.get('ActiveState', 'inactive'),
            'pid': int(values.get('MainPID', '0')), 'result': values.get('Result', '')}


def player_command(kind, folder):
    if kind not in ('mp3', 'mp4'):
        raise ValueError('Choose mp3 or mp4.')
    media = folder / ('demo.' + kind)
    if not media.is_file() or media.stat().st_size == 0:
        raise RuntimeError('Demo media file is missing: ' + str(media))
    player = shutil.which('ffplay')
    if not player:
        raise RuntimeError('ffplay is missing; install the Ubuntu ffmpeg package.')
    command = [player, '-autoexit', '-loglevel', 'error', '-nostats',
               '-volume', '65', '-window_title', 'Intel Local Media Demo']
    if kind == 'mp3':
        command += ['-nodisp']
    else:
        command += ['-x', '960', '-y', '540']
    return command + [str(media)]


def control(action):
    if action == 'status':
        return status()
    if action == 'stop':
        before = status()
        if before['state'] in ('active', 'activating'):
            subprocess.run(['systemctl', '--user', 'stop', UNIT], check=True, timeout=8)
        return {**status(), 'action': 'stopped'}
    command = player_command(action, Path.home() / 'hermes-demo-media')
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
    parser.add_argument('action', choices=['mp3', 'mp4', 'stop', 'status'])
    args = parser.parse_args()
    try:
        result = control(args.action)
    except Exception as exc:
        result = {'success': False, 'error': str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
