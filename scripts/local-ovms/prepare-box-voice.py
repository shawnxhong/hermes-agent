#!/usr/bin/env python3
"""Generate two local lifecycle clips, without a cloud API or playback."""
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    home = Path(os.environ.get('HERMES_HOME', str(Path.home()/'.hermes')))
    folder = home / 'cache/box-voice'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name, text in [('standby', 'Intel AI Box agent standing by'),
                       ('goodbye', 'See you next time')]:
        with tempfile.TemporaryDirectory(prefix='generate-', dir=folder) as temporary:
            source = Path(temporary)/'text.txt'
            audio = Path(temporary)/'audio.wav'
            source.write_text(text, encoding='utf-8')
            subprocess.run([str(home/'kokoro-venv/bin/python'),
                str(Path.home()/'hermes-ovms-setup/kokoro-zh-tts.py'),
                '--input', str(source), '--output', str(audio), '--voice', 'zf_001',
                '--english-voice', 'af_maple', '--speed', '1.0'], check=True, timeout=120)
            if audio.stat().st_size <= 44:
                raise RuntimeError('Empty lifecycle audio')
            audio.replace(folder/(name+'.wav'))
            print(name + ': ' + str(folder/(name+'.wav')), flush=True)


if __name__ == '__main__':
    main()
