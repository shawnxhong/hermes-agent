"""Execute the shell launcher with an isolated fake CLI, never user config."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.linux_only
@pytest.mark.parametrize('extra', [[], ['--skills', 'travel-concierge']])
def test_voice_mode_only_preloads_explicit_skills(tmp_path, extra):
    fake = tmp_path / 'hermes'
    fake.write_text(f'#!{sys.executable}\n' + '''import json, sys
if sys.argv[1:3] == ['config', 'get']:
    print('true')
elif sys.argv[1:2] == ['--cli']:
    print('CLI_ARGS=' + json.dumps(sys.argv[1:]))
''')
    fake.chmod(0o700)
    script = Path(__file__).resolve().parents[2] / 'scripts/local-ovms/hermes-mode'
    result = subprocess.run(['bash', str(script), 'voice', '--run', *extra],
                            env={**os.environ, 'HERMES_BIN': str(fake)},
                            text=True, capture_output=True, check=True)
    line = next(x for x in result.stdout.splitlines() if x.startswith('CLI_ARGS='))
    assert json.loads(line.split('=', 1)[1]) == ['--cli', *extra]
