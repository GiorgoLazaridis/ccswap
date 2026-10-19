"""Run the menu bar from inside a wrapper ``.app`` so the process has a bundle ID.

``cswap menubar`` is a console script: the process macOS sees is a bare Python
interpreter somewhere under ``~/.local/share/uv`` (or a pipx venv), and
``NSRunningApplication.bundleIdentifier`` for it is nil. Menu bar managers key
every status item by that identifier (Dimble, Ice, Bartender, SaneBar all do),
and an item whose owner has none is simply skipped: it never shows up in their
panel and cannot be hidden, ordered or given a hotkey.

The fix is a minimal bundle built on demand under the data directory::

    claude-swap Menubar.app/
      Contents/
        Info.plist          CFBundleIdentifier com.claude-swap.menubar, LSUIElement
        pyvenv.cfg          home = <directory of the real interpreter>
        lib -> <sys.prefix>/lib          symlink: the install's site-packages
        MacOS/
          python            byte copy of the real interpreter
          claude-swap       sh launcher, for Finder / `open`

LaunchServices identifies a process by its executable's path, so the
interpreter must physically live under ``Contents/MacOS`` (a symlink resolves to
the original and keeps the nil identity; a copy is ~20 MB, once). CPython then
finds ``pyvenv.cfg`` one directory up and treats ``Contents`` as its venv:
``home`` points it at the stdlib, the ``lib`` symlink at the installed packages.
So the copy runs the very same claude-swap, just under a name macOS can see.

The bundle is rebuilt (cheaply: only what changed) on every ``cswap menubar``
start and the process re-execs into it before AppKit loads. ``uv tool
--reinstall`` recreating the venv or a managed-Python version bump therefore
never leaves a stale bundle behind, and launchd keeps the same pid throughout.
"""

from __future__ import annotations

import os
import plistlib
import stat
import sys
import tempfile
from pathlib import Path

from claude_swap.printer import warning

BUNDLE_ID = "com.claude-swap.menubar"
BUNDLE_NAME = "claude-swap"
BUNDLE_DIRNAME = "claude-swap Menubar.app"
OPT_OUT_ENV = "CLAUDE_SWAP_NO_BUNDLE"

_LAUNCHER = """#!/bin/sh
# Finder / `open` entry point. launchd and `cswap menubar` exec the python
# next to this script directly; both end up as the same bundled process.
exec "$(dirname "$0")/python" -m claude_swap menubar
"""


def bundle_path(backup_dir: Path) -> Path:
    """Where the wrapper bundle lives for this data directory."""
    return Path(backup_dir) / BUNDLE_DIRNAME


def in_bundle(executable: Path | None = None) -> bool:
    """Whether ``executable`` (default: this interpreter) runs inside our bundle."""
    exe = Path(executable or sys.executable)
    contents = exe.parent.parent
    if contents.name != "Contents" or contents.parent.suffix != ".app":
        return False
    try:
        info = plistlib.loads((contents / "Info.plist").read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError):
        return False
    return isinstance(info, dict) and info.get("CFBundleIdentifier") == BUNDLE_ID


def _same_file_content(src: Path, dst: Path) -> bool:
    """Cheap staleness check: a copy made with mtime preserved matches on both."""
    try:
        a, b = src.stat(), dst.stat()
    except OSError:
        return False
    return a.st_size == b.st_size and a.st_mtime_ns == b.st_mtime_ns


def _write_if_changed(path: Path, data: bytes, *, executable: bool = False) -> None:
    """Atomic write, skipped when the file already holds ``data``."""
    try:
        if path.read_bytes() == data:
            return
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    mode = 0o755 if executable else 0o644
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _copy_if_changed(src: Path, dst: Path) -> None:
    """Copy ``src`` over ``dst`` atomically, preserving mode and mtime.

    A running instance keeps executing the old inode, so replacing the file
    under it is safe; writing into it in place would not be.
    """
    if _same_file_content(src, dst):
        return
    fd, tmp = tempfile.mkstemp(dir=dst.parent, prefix=f".{dst.name}.")
    try:
        with os.fdopen(fd, "wb") as out, src.open("rb") as inp:
            while chunk := inp.read(1 << 20):
                out.write(chunk)
        st = src.stat()
        os.chmod(tmp, stat.S_IMODE(st.st_mode) | 0o700)
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(tmp, dst)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _symlink_if_changed(target: Path, link: Path) -> None:
    if link.is_symlink() and os.readlink(link) == str(target):
        return
    tmp = link.with_name(f".{link.name}.{os.getpid()}")
    if tmp.is_symlink():
        tmp.unlink()
    os.symlink(str(target), tmp)
    if link.exists() and not link.is_symlink():
        raise OSError(f"{link} exists and is not a symlink")
    os.replace(tmp, link)


def build(
    backup_dir: Path,
    *,
    executable: Path | None = None,
    prefix: Path | None = None,
) -> Path:
    """Create or refresh the bundle; returns the bundled interpreter's path.

    ``executable`` is the real interpreter to copy (default: the base
    interpreter behind this process, through any venv symlink) and ``prefix``
    the install whose ``lib`` holds claude-swap (default: ``sys.prefix``).
    Idempotent: unchanged parts are left untouched.
    """
    src = Path(
        executable or getattr(sys, "_base_executable", None) or sys.executable
    ).resolve()
    lib_target = Path(prefix or sys.prefix) / "lib"

    contents = bundle_path(backup_dir) / "Contents"
    macos = contents / "MacOS"
    macos.mkdir(parents=True, exist_ok=True)

    _write_if_changed(
        contents / "Info.plist",
        plistlib.dumps(
            {
                "CFBundleIdentifier": BUNDLE_ID,
                "CFBundleName": BUNDLE_NAME,
                "CFBundleExecutable": "claude-swap",
                "CFBundlePackageType": "APPL",
                "LSUIElement": True,
            }
        ),
    )
    # `home` is the directory holding the base interpreter; CPython derives
    # the stdlib location from it exactly as it does for a normal venv.
    _write_if_changed(
        contents / "pyvenv.cfg",
        f"home = {src.parent}\ninclude-system-site-packages = false\n".encode(),
    )
    _symlink_if_changed(lib_target, contents / "lib")
    _write_if_changed(macos / "claude-swap", _LAUNCHER.encode(), executable=True)
    python = macos / "python"
    _copy_if_changed(src, python)
    return python


def reexec_reason(platform: str = sys.platform, environ=None) -> str | None:
    """Why this process should NOT re-exec into the bundle; None when it should."""
    environ = os.environ if environ is None else environ
    if platform != "darwin":
        return "not macOS"
    if environ.get(OPT_OUT_ENV):
        return f"{OPT_OUT_ENV} is set"
    if getattr(sys, "_framework", ""):
        # A framework build's bin/python is a stub that spawns Python.app from
        # a path relative to itself; a copy of the stub finds nothing. The
        # menu bar's framework_build_warning already covers these installs.
        return "framework build of Python"
    if in_bundle():
        return "already in the bundle"
    return None


def maybe_reexec(backup_dir: Path, argv: list[str] | None = None) -> str | None:
    """Replace this process with the same command run from inside the bundle.

    Returns only when staying put, with the reason; on success ``execv`` never
    returns. A bundle that cannot be built falls back to the plain interpreter
    (the menu bar still works, it just stays invisible to menu bar managers).
    """
    reason = reexec_reason()
    if reason:
        return reason
    try:
        python = build(backup_dir)
    except OSError as exc:
        reason = f"could not build {bundle_path(backup_dir)}: {exc}"
        warning(
            f"Menu bar managers will not see this icon ({reason}); "
            "the menu bar itself works as before.",
            file=sys.stderr,
        )
        return reason
    args = sys.argv[1:] if argv is None else argv
    os.execv(str(python), [str(python), "-m", "claude_swap", *args])
    return None  # pragma: no cover - execv does not return
