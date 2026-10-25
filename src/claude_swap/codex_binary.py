"""Internal native binary resolution and credential-free compatibility probes."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path

from claude_swap.exceptions import SessionError

_VERSION = "0.164.0"
_LIMIT = 128 * 1024
_TIMEOUT = 5
_LAYOUT = "Cannot resolve the Codex installation. Put the native codex.exe on PATH."
_TARGETS = {
    "x64": "x86_64-pc-windows-msvc",
    "arm64": "aarch64-pc-windows-msvc",
}


def _metadata(package: Path, name: str) -> None:
    data = json.loads((package / "package.json").read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("name") != name or data.get("version") != _VERSION:
        raise SessionError(_LAYOUT)


def _platform_package(package: Path, arch: str) -> Path | None:
    # createRequire(bin/codex.js): bin/node_modules first, then each parent,
    # skipping node_modules/node_modules, as Node's module path algorithm does.
    for parent in (package / "bin", package, *package.parents):
        if parent.name == "node_modules":
            continue
        candidate = parent / "node_modules" / "@openai" / f"codex-win32-{arch}"
        try:
            (candidate / "package.json").stat()
        except FileNotFoundError:
            continue
        return candidate.resolve(strict=True)
    return None


def _windows_arches(env: Mapping[str, str]) -> list[str]:
    aliases = {"amd64": "x64", "arm64": "arm64", "x86": "x86"}
    process = aliases.get(env.get("PROCESSOR_ARCHITECTURE", "").casefold())
    wow = env.get("PROCESSOR_ARCHITEW6432", "").casefold()
    if wow and wow not in aliases:
        raise SessionError(_LAYOUT)
    native = aliases.get(wow) or process
    if process == "arm64" and native != "arm64":
        raise SessionError(_LAYOUT)
    if native == "x64":
        return ["x64"]
    if native == "arm64":
        # Both native and emulated Node installations are plausible on ARM64.
        return ["arm64", "x64"]
    raise SessionError(_LAYOUT)


def resolve_codex_binary(env: Mapping[str, str]) -> list[str]:
    """Resolve one PATH installation, without ever executing Windows shims."""
    try:
        found = shutil.which("codex", path=env.get("PATH", ""))
        if not found:
            raise SessionError(_LAYOUT)
        binary = Path(found).resolve(strict=True)
        if not binary.is_file():
            raise SessionError(_LAYOUT)
        if os.name != "nt":
            return [str(binary)]
        if binary.name.casefold() == "codex.exe":
            return [str(binary)]
        if binary.suffix.casefold() not in {".cmd", ".bat", ".ps1"}:
            raise SessionError(_LAYOUT)
        package = (binary.parent / "node_modules" / "@openai" / "codex").resolve(strict=True)
        _metadata(package, "@openai/codex")
        if not (package / "bin" / "codex.js").is_file():
            raise SessionError(_LAYOUT)
        candidates = []
        for arch in _windows_arches(env):
            platform = _platform_package(package, arch)
            root = platform if platform is not None else package
            executable = root / "vendor" / _TARGETS[arch] / "bin" / "codex.exe"
            if platform is not None or executable.exists():
                # A resolved but broken installation is uncertainty, not absence.
                if not executable.is_file():
                    raise SessionError(_LAYOUT)
                candidates.append(executable.resolve(strict=True))
        if len(candidates) != 1:
            raise SessionError(_LAYOUT)
        return [str(candidates[0])]
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        raise SessionError(_LAYOUT) from exc


def probe_codex(argv_prefix: list[str]) -> None:
    """Require the supported version and root no-daemon capability in an empty home."""
    if not argv_prefix or any(not isinstance(arg, str) or not arg for arg in argv_prefix):
        raise SessionError("Invalid Codex binary prefix.")
    try:
        with tempfile.TemporaryDirectory(prefix="ccswap-probe-") as directory:
            env = {key: value for key, value in os.environ.items()
                   if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
            env.update(HOME=directory, USERPROFILE=directory, CODEX_HOME=directory)
            for option in ("--version", "--help"):
                output = _probe_output(argv_prefix + [option], env)
                if option == "--version":
                    if re.fullmatch(rb"codex-cli 0\.164\.0\s*", output) is None:
                        raise SessionError("Session mode requires Codex 0.164.0.")
                elif re.search(rb"(?<![\w-])--no-daemon(?![\w-])", output) is None:
                    raise SessionError("Codex does not advertise --no-daemon.")
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise SessionError("Codex compatibility probe failed.") from exc


def _probe_output(argv: list[str], env: dict[str, str]) -> bytes:
    # Read pipes incrementally: neither retained output nor a temporary output file
    # can grow without bound. communicate()/run(capture_output=True) cannot do this.
    chunks = bytearray()
    oversized = threading.Event()
    with subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL,
                          # Codex warns on stderr about a temporary home; only stdout is parsed.
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          shell=False, bufsize=0) as process:
        def drain() -> None:
            try:
                while block := process.stdout.read(4096):
                    if len(chunks) + len(block) > _LIMIT:
                        oversized.set()
                        process.kill()
                        return
                    chunks.extend(block)
            except OSError:
                oversized.set()  # An incomplete read cannot prove compatibility.
                process.kill()

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        try:
            result = process.wait(timeout=_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise
        finally:
            reader.join(timeout=_TIMEOUT)
        if reader.is_alive() or oversized.is_set() or result:
            raise SessionError("Codex compatibility probe failed.")
    return bytes(chunks)
