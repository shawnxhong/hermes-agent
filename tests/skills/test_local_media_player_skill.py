import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest

PATH = Path(__file__).resolve().parents[2] / 'scripts/local-ovms/skills/local-media-player/scripts/play_media.py'
spec = importlib.util.spec_from_file_location('media_player', PATH)
media = importlib.util.module_from_spec(spec)
spec.loader.exec_module(media)


def test_fixed_files_and_no_shell(tmp_path, monkeypatch):
    monkeypatch.setattr(media.shutil, 'which', lambda _: '/usr/bin/ffplay')
    for kind in ('mp3', 'mp4'):
        (tmp_path / ('demo.' + kind)).write_bytes(b'media fixture')
        cmd = media.player_command(kind, tmp_path)
        assert cmd[-1] == str(tmp_path / ('demo.' + kind))
        assert ('-nodisp' in cmd) == (kind == 'mp3')
        assert '-autoexit' in cmd
    with pytest.raises(ValueError):
        media.player_command('https://example.com', tmp_path)


def test_missing_file_fails(tmp_path):
    with pytest.raises(RuntimeError, match='missing'):
        media.player_command('mp3', tmp_path)


def test_play_resolves_desktop_before_starting_player(tmp_path, monkeypatch):
    monkeypatch.setattr(media.Path, 'home', lambda: tmp_path)
    def check(kind, folder):
        assert kind == 'mp4'
        assert folder == tmp_path / 'Desktop'
        raise RuntimeError('checked target')
    monkeypatch.setattr(media, 'player_command', check)
    with pytest.raises(RuntimeError, match='checked target'):
        media.control('mp4')


def test_status_and_stop_only_owned_unit(monkeypatch):
    calls = []
    def run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout='ActiveState=active\nMainPID=123\nResult=success\n')
    monkeypatch.setattr(media.subprocess, 'run', run)
    assert media.control('stop')['success']
    assert calls[1] == ['systemctl', '--user', 'stop', media.UNIT]
