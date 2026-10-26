"""A failed directory scan must never authorize a profile mutation."""
import os
import json
from pathlib import Path

import pytest

from claude_swap.process_detection import list_sessions, scan_sessions
from claude_swap import process_detection
from claude_swap.session import profile_is_quiescent, scan_live_sessions
from claude_swap.exceptions import SessionError
from claude_swap.switcher import ClaudeAccountSwitcher


def fail_enumeration(monkeypatch, directory, error):
    # Inject at the OS boundary used by iterdir: listdir on Python 3.12,
    # scandir on newer versions. No filesystem permission/root assumptions.
    for name in ("listdir", "scandir"):
        original = getattr(os, name)

        def listing(path, *args, _original=original, **kwargs):
            if path == directory or path == str(directory):
                raise error("synthetic directory enumeration failure")
            return _original(path, *args, **kwargs)

        monkeypatch.setattr(os, name, listing)


@pytest.mark.parametrize("error", [PermissionError, OSError, NotADirectoryError])
def test_directory_scan_failure_is_not_quiescent(temp_home, monkeypatch, error):
    switcher = ClaudeAccountSwitcher()
    profile = switcher._session_dir("2", "test@example.com")
    sessions_dir = profile / "sessions"
    sessions_dir.mkdir(parents=True)
    fail_enumeration(monkeypatch, sessions_dir, error)
    assert scan_sessions(profile) == ([], 1)
    assert scan_live_sessions(profile) == ([], 1)
    assert not profile_is_quiescent(profile)
    with pytest.raises(SessionError, match="could not be read"):
        switcher._ensure_no_live_session("2", "test@example.com", "the operation")
    # Read-only listings remain tolerant instead of raising filesystem errors.
    assert list_sessions(profile) == []


def test_profile_existence_probe_cannot_hide_scan_failure(tmp_path, monkeypatch):
    """Path.exists() can suppress errors; the scanner must decide absence."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    original_exists = Path.exists

    def exists(path):
        # Model a suppressed filesystem error at the outer profile check.
        return False if path == tmp_path else original_exists(path)

    monkeypatch.setattr(Path, "exists", exists)
    fail_enumeration(monkeypatch, sessions_dir, PermissionError)
    assert scan_live_sessions(tmp_path) == ([], 1)
    assert not profile_is_quiescent(tmp_path)


def test_directory_scan_preserves_platform_glob_matching(tmp_path, monkeypatch):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    for name in (".json", "lower.json", "upper.JSON", "record.json.tmp"):
        (sessions_dir / name).write_text(
            json.dumps({"pid": 1, "sessionId": name}), encoding="utf-8")
    expected = sorted(path.name for path in sessions_dir.glob("*.json"))
    monkeypatch.setattr(process_detection, "is_pid_alive", lambda pid: True)
    monkeypatch.setattr(process_detection, "pid_matches_record", lambda *args: True)
    sessions, unreadable = scan_sessions(tmp_path)
    assert sorted(session.session_id for session in sessions) == expected
    assert unreadable == 0
