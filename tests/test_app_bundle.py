"""Tests for the wrapper .app that gives the menu bar a bundle identifier.

Everything is built from fake interpreters inside ``tmp_path``; ``os.execv``
is patched so no test ever replaces the pytest process.
"""

from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path
from unittest.mock import patch

from claude_swap import app_bundle


def _fake_install(tmp_path: Path, blob: bytes = b"#!fake python\n") -> tuple[Path, Path]:
    """A pretend base interpreter and a pretend venv prefix."""
    base_bin = tmp_path / "base" / "bin"
    base_bin.mkdir(parents=True)
    exe = base_bin / "python3.15"
    exe.write_bytes(blob)
    exe.chmod(0o755)
    prefix = tmp_path / "venv"
    (prefix / "lib" / "python3.15" / "site-packages").mkdir(parents=True)
    return exe, prefix


def test_build_lays_out_a_bundle_around_a_copy_of_the_interpreter(tmp_path: Path):
    exe, prefix = _fake_install(tmp_path)
    backup = tmp_path / "data"

    python = app_bundle.build(backup, executable=exe, prefix=prefix)

    contents = app_bundle.bundle_path(backup) / "Contents"
    assert python == contents / "MacOS" / "python"
    assert python.read_bytes() == exe.read_bytes()
    assert not python.is_symlink()  # a symlink would resolve to the bare interpreter
    assert os.access(python, os.X_OK)

    info = plistlib.loads((contents / "Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == app_bundle.BUNDLE_ID
    assert info["CFBundleExecutable"] == "claude-swap"
    assert info["LSUIElement"] is True

    cfg = (contents / "pyvenv.cfg").read_text()
    assert f"home = {exe.parent}\n" in cfg

    lib = contents / "lib"
    assert lib.is_symlink() and os.readlink(lib) == str(prefix / "lib")
    assert (lib / "python3.15" / "site-packages").is_dir()

    launcher = contents / "MacOS" / "claude-swap"
    assert os.access(launcher, os.X_OK)
    assert "-m claude_swap menubar" in launcher.read_text()


def test_rebuild_leaves_an_unchanged_bundle_alone(tmp_path: Path):
    exe, prefix = _fake_install(tmp_path)
    backup = tmp_path / "data"
    python = app_bundle.build(backup, executable=exe, prefix=prefix)
    before = python.stat().st_ino

    app_bundle.build(backup, executable=exe, prefix=prefix)

    assert python.stat().st_ino == before


def test_rebuild_refreshes_a_changed_interpreter(tmp_path: Path):
    exe, prefix = _fake_install(tmp_path)
    backup = tmp_path / "data"
    python = app_bundle.build(backup, executable=exe, prefix=prefix)
    old_ino = python.stat().st_ino

    exe.write_bytes(b"#!fake python 3.16, longer\n")  # as after a uv python bump
    app_bundle.build(backup, executable=exe, prefix=prefix)

    assert python.read_bytes() == exe.read_bytes()
    assert python.stat().st_ino != old_ino  # replaced, never written in place


def test_rebuild_repoints_lib_and_home_after_a_reinstall(tmp_path: Path):
    exe, prefix = _fake_install(tmp_path)
    backup = tmp_path / "data"
    app_bundle.build(backup, executable=exe, prefix=prefix)

    new_exe = tmp_path / "base2" / "bin" / "python3.16"
    new_exe.parent.mkdir(parents=True)
    new_exe.write_bytes(exe.read_bytes())
    new_prefix = tmp_path / "venv2"
    (new_prefix / "lib").mkdir(parents=True)
    app_bundle.build(backup, executable=new_exe, prefix=new_prefix)

    contents = app_bundle.bundle_path(backup) / "Contents"
    assert os.readlink(contents / "lib") == str(new_prefix / "lib")
    assert f"home = {new_exe.parent}\n" in (contents / "pyvenv.cfg").read_text()


def test_in_bundle_recognises_only_our_bundle(tmp_path: Path):
    exe, prefix = _fake_install(tmp_path)
    python = app_bundle.build(tmp_path / "data", executable=exe, prefix=prefix)
    assert app_bundle.in_bundle(python)
    assert not app_bundle.in_bundle(exe)

    other = tmp_path / "Other.app" / "Contents" / "MacOS"
    other.mkdir(parents=True)
    (other.parent / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleIdentifier": "com.example.other"})
    )
    assert not app_bundle.in_bundle(other / "python")


def test_reexec_reason_lists_every_way_to_stay_put(tmp_path: Path):
    assert app_bundle.reexec_reason(platform="linux") == "not macOS"
    assert app_bundle.reexec_reason(
        platform="darwin", environ={app_bundle.OPT_OUT_ENV: "1"}
    ) == f"{app_bundle.OPT_OUT_ENV} is set"
    with patch.object(sys, "_framework", "Python", create=True):
        assert app_bundle.reexec_reason(platform="darwin", environ={}) == (
            "framework build of Python"
        )
    with patch.object(sys, "_framework", "", create=True), patch.object(
        app_bundle, "in_bundle", return_value=True
    ):
        assert app_bundle.reexec_reason(platform="darwin", environ={}) == (
            "already in the bundle"
        )


def test_maybe_reexec_execs_the_same_command_inside_the_bundle(tmp_path: Path):
    exe, prefix = _fake_install(tmp_path)
    backup = tmp_path / "data"
    calls: list[tuple[str, list[str]]] = []
    real_build = app_bundle.build

    with patch.object(app_bundle, "reexec_reason", return_value=None), patch.object(
        app_bundle, "build", lambda b: real_build(b, executable=exe, prefix=prefix)
    ), patch.object(app_bundle.os, "execv", lambda p, a: calls.append((p, a))), patch.object(
        sys, "argv", ["/x/bin/cswap", "menubar"]
    ):
        app_bundle.maybe_reexec(backup)

    python = str(app_bundle.bundle_path(backup) / "Contents" / "MacOS" / "python")
    assert calls == [(python, [python, "-m", "claude_swap", "menubar"])]


def test_maybe_reexec_falls_back_when_the_bundle_cannot_be_built(tmp_path: Path, capsys):
    calls: list = []
    with patch.object(app_bundle, "reexec_reason", return_value=None), patch.object(
        app_bundle, "build", side_effect=OSError("disk full")
    ), patch.object(app_bundle.os, "execv", lambda p, a: calls.append((p, a))):
        reason = app_bundle.maybe_reexec(tmp_path / "data")

    assert calls == []
    assert reason is not None and "disk full" in reason
    assert "disk full" in capsys.readouterr().err
