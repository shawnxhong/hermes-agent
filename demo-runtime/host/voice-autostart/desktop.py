"""Desktop-only boot glue; no agent, prompt, audio-routing or network changes."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import time
import urllib.request


def runtime():
    text = (Path.home() / '.local/bin/hermes-box-voice').read_text()
    found = re.search(r'^export HERMES_CONTAINER_CONTROL=([^\s]+)$', text, re.M)
    if not found:
        raise RuntimeError('Expected the managed Docker voice entrypoint')
    return Path(found[1]).parent


def compose(root):
    c = json.loads((root / 'control.json').read_text())
    return ['docker', 'compose', '--env-file', c['env_file'], '-f', c['compose_file']] + [
        value for p in c.get('overlays', []) for value in ('-f', p)]


def auxiliaries():
    root = runtime()
    services = subprocess.check_output(compose(root) + ['config', '--services'], text=True).split()
    if 'quiet-fan' in services:
        subprocess.run(compose(root) + ['up', '-d', '--no-deps', 'quiet-fan'], check=True, timeout=90)


def ready(root):
    # Read-only checks; do not take a live CLI's EOS subscription.
    with urllib.request.urlopen('http://127.0.0.1:8000/v3/models', timeout=3) as response:
        if not json.load(response).get('data'):
            return False
    for rel in ('ipc/asr/asr.sock', 'ipc/tts/tts.sock', 'ipc/wake/service.sock', 'ipc/media/service.sock'):
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(2)
            client.connect(str(root / rel))
    return True


def prepare():
    print('Waiting for background services and desktop audio...', flush=True)
    subprocess.run(['systemctl', '--user', 'start', 'hermes-production.service'], check=True, timeout=620)
    root = runtime()
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            if (Path('/run/user') / str(os.getuid()) / 'pipewire-0').is_socket() and ready(root):
                print('Background services ready.', flush=True)
                return
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise TimeoutError('Background readiness timed out; no voice CLI was started')


def launch():
    session = str(Path(__file__).resolve().parent / 'session.sh')
    if shutil.which('gnome-terminal'):
        command = ['gnome-terminal', '--title=Hermes Voice', '--', '/bin/bash', session]
    elif shutil.which('ptyxis'):
        command = ['ptyxis', '--new-window', '--title=Hermes Voice', '--', '/bin/bash', session]
    else:
        raise RuntimeError('GNOME Terminal or Ptyxis is required')
    # A first Ptyxis instance can own the application lifetime. Do not put a
    # short subprocess timeout around the terminal and kill the user's shell.
    os.execvp(command[0], command)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['launch', 'prepare', 'background-start', 'background-stop', 'auxiliaries'])
    action = p.parse_args().action
    if action in ('background-start', 'background-stop'):
        root = runtime()
        subprocess.run(['/usr/bin/python3', str(root / 'lifecycle.py'), action.split('-')[1],
                        '--root', str(root)], check=True, timeout=550)
    else:
        globals()[action]()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(130)
