"""The host mode launcher must preserve the caller's network selection."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.linux_only
@pytest.mark.parametrize("override", [None, "", "http://explicit.example:8080"])
@pytest.mark.parametrize(
    ("mode", "expected_argv"),
    [
        ("voice", ["--cli", "--skills", "travel-concierge"]),
        ("keyboard", ["--cli"]),
    ],
)
def test_mode_launcher_proxy_environment_and_skills(
    tmp_path, override, mode, expected_argv
):
    fake = tmp_path / "hermes"
    fake.write_text(
        "#!" + sys.executable + "\n"
        "import os, json, sys\n"
        "if sys.argv[1:2] == ['--cli']:\n"
        " values={k:os.environ.get(k) for k in "
        "['http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY','NO_PROXY','no_proxy']}\n"
        " values['argv']=sys.argv[1:]\n"
        " print(json.dumps(values))\n"
        "elif sys.argv[1:3] == ['config','get']: print('true')\n"
    )
    fake.chmod(0o700)
    env = {
        "PATH": os.environ["PATH"], "HOME": str(tmp_path),
        "HERMES_BIN": str(fake), "http_proxy": "http://personal.example:7897",
        "NO_PROXY": "existing.example",
    }
    if override is not None:
        env["HERMES_PROXY_URL"] = override
    launcher = Path(__file__).resolve().parents[2] / "scripts/local-ovms/hermes-mode"
    result = subprocess.run(
        ["bash", str(launcher), mode, "--run"], env=env,
        capture_output=True, text=True, check=True, timeout=10,
    )
    values = json.loads(result.stdout.splitlines()[-1])
    assert values["argv"] == expected_argv
    assert values["http_proxy"] == (override or "http://personal.example:7897")
    assert values["HTTPS_PROXY"] == (override or None)
    assert "existing.example" in values["NO_PROXY"].split(",")
    for key in ("NO_PROXY", "no_proxy"):
        assert {"localhost", "127.0.0.1", "::1"} <= set(values[key].split(","))
