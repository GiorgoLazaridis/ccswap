"""_write_json must publish the roster with an atomic replace (issue #295).

shutil.move falls back to copy+unlink on Windows whenever the destination
exists, rewriting sequence.json in place; readers could then see a torn file.
"""

import json
import os
import shutil

import pytest

from claude_swap.switcher import ClaudeAccountSwitcher


def _roster(n: int) -> dict:
    return {"activeAccountNumber": n, "lastUpdated": "", "sequence": [n], "accounts": {}}


def test_overwrite_uses_atomic_replace_not_copy(temp_home, monkeypatch):
    s = ClaudeAccountSwitcher()
    s.sequence_file.parent.mkdir(parents=True, exist_ok=True)
    s._write_json(s.sequence_file, _roster(1))

    def _no_copy(*_a, **_k):
        raise AssertionError("roster published via copy, not atomic replace")

    monkeypatch.setattr(shutil, "move", _no_copy)
    monkeypatch.setattr(shutil, "copy2", _no_copy)
    s._write_json(s.sequence_file, _roster(2))

    assert json.loads(s.sequence_file.read_text(encoding="utf-8"))["activeAccountNumber"] == 2
    assert not list(s.sequence_file.parent.glob("*.tmp"))


def test_failed_replace_keeps_old_roster_and_removes_temp(temp_home, monkeypatch):
    s = ClaudeAccountSwitcher()
    s.sequence_file.parent.mkdir(parents=True, exist_ok=True)
    s._write_json(s.sequence_file, _roster(1))

    def _fail(*_a, **_k):
        raise PermissionError("locked")

    monkeypatch.setattr(os, "replace", _fail)
    with pytest.raises(PermissionError):
        s._write_json(s.sequence_file, _roster(2))

    assert json.loads(s.sequence_file.read_text(encoding="utf-8"))["activeAccountNumber"] == 1
    assert not list(s.sequence_file.parent.glob("*.tmp"))
