"""Cold-start EOS readiness only: a probe must never steal a live CLI subscription."""
import subprocess
import time


def running(name):
    p = subprocess.run(['docker', 'inspect', '--format', '{{.State.Running}}', name],
                       capture_output=True, text=True, timeout=10)
    if p.returncode:
        if 'no such container' in p.stderr.lower() or 'no such object' in p.stderr.lower():
            return False
        raise RuntimeError('Cannot inspect voice container; refusing a subscription probe')
    if p.stdout.strip() not in ('true', 'false'):
        raise RuntimeError('Unexpected Docker container state')
    return p.stdout.strip() == 'true'


def await_eos(compose, voice_name, timeout=90):
    if running(voice_name):
        return
    subprocess.run(compose + ['up', '-d', '--no-deps', 'aisp-wake'], check=True, timeout=60)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if running(voice_name):
            return
        try:
            p = subprocess.run(compose + ['exec', '-T', 'aisp-wake', '/usr/bin/python3',
                                         '/opt/qwen-wake/check_startup.py'],
                               capture_output=True, text=True, timeout=6)
            if p.returncode == 0:
                print('AISP EOS service ready', flush=True)
                return
        except subprocess.TimeoutExpired:
            pass
        time.sleep(.5)
    raise TimeoutError('AISP EOS subscription did not become ready; CLI not started')
