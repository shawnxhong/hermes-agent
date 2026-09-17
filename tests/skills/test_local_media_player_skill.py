import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest

PATH = Path(__file__).resolve().parents[2] / 'scripts/local-ovms/plugins/demo-media/play_media.py'
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
    (tmp_path / 'Desktop').mkdir()
    (tmp_path / 'Desktop' / 'movie.mp4').write_bytes(b'media fixture')
    def check(kind, folder, target):
        assert kind == 'mp4'
        assert folder == tmp_path / 'Desktop'
        assert target == 'movie.mp4'
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


def test_catalog_and_case_space_partial_matching(tmp_path, monkeypatch):
    monkeypatch.setattr(media.shutil, 'which', lambda _: '/usr/bin/ffplay')
    for name in ['Dragonfly Pro V2.mp4', 'south_song.mp3', 'left_right.wav', 'notes.txt', '.hidden.mp4']:
        (tmp_path / name).write_bytes(b'fixture')
    assert {f['name'] for f in media.catalog(tmp_path)} == {'Dragonfly Pro V2.mp4', 'south_song.mp3', 'left_right.wav'}
    assert media.player_command('play', tmp_path, 'DRAGONFLY')[-1].endswith('Dragonfly Pro V2.mp4')
    cmd = media.player_command('play', tmp_path, 'left right')
    assert '-nodisp' in cmd


def test_ambiguous_missing_and_paths_never_start_player(tmp_path, monkeypatch):
    desktop = tmp_path / 'Desktop'
    desktop.mkdir()
    for name in ['one.mp4', 'two.mp4']:
        (desktop / name).write_bytes(b'fixture')
    (tmp_path / 'outside.mp4').write_bytes(b'private')
    (desktop / 'link.mp4').symlink_to(tmp_path / 'outside.mp4')
    monkeypatch.setattr(media.Path, 'home', lambda: tmp_path)
    def fail(*a, **kw):
        pytest.fail('must not launch or stop any player')
    monkeypatch.setattr(media.subprocess, 'run', fail)
    assert media.control('mp4')['error'] == 'ambiguous'
    for target in ['missing', '../outside.mp4', '/tmp/movie.mp4', 'link.mp4']:
        assert media.control('play', target)['error'] == 'not_found'
    assert len(media.control('list')['files']) == 2


def test_filename_is_single_argv_not_shell(tmp_path, monkeypatch):
    name = 'movie; touch OWNED.mp4'
    (tmp_path / name).write_bytes(b'fixture')
    monkeypatch.setattr(media.shutil, 'which', lambda _: '/usr/bin/ffplay')
    assert media.player_command('play', tmp_path, name)[-1] == str(tmp_path / name)
