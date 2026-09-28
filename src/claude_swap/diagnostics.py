"""Local, non-invasive diagnostics for native CLI installations."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from claude_swap import __version__
from claude_swap.settings import SETTINGS_SCHEMA_VERSION
from claude_swap.usage_store import STALE_OK_S, UsageStore


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # ok | warning | error | info
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


def _endpoint(value: str) -> str:
    """Never echo URL credentials, paths, queries or fragments."""
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return "configured (value hidden)"
        port = f":{parsed.port}" if parsed.port is not None else ""
        return f"{parsed.scheme}://{parsed.hostname}{port}"
    except ValueError:
        return "configured (value hidden)"


def _cli_check(name: str) -> Check:
    program = shutil.which(name)
    if not program:
        return Check(name, "warning", "not found on PATH")
    try:
        result = subprocess.run(
            [program, "--version"], capture_output=True, text=True,
            timeout=4, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return Check(name, "warning", "found, version probe failed")
    # Show only a version number. A wrapper's arbitrary output must not end up
    # in JSON diagnostics or a copied support report.
    match = re.search(r"\b\d+\.\d+(?:\.\d+)?\b", result.stdout)
    if result.returncode != 0 or match is None:
        return Check(name, "warning", "found, version unknown")
    return Check(name, "ok", match.group(0))


def _install_dirs(name: str, env: dict[str, str]) -> list[str]:
    """Distinct PATH directories that provide ``name``, in lookup order.

    A native install next to an npm shim (common on Windows: ``claude.exe``
    in ``~/.local/bin`` and ``claude.cmd`` under the Node prefix) means the
    first PATH entry silently wins and updates may land in the other copy.
    """
    windows = sys.platform == "win32"
    exts = [""]
    if windows:
        exts = [e.lower() for e in env.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";") if e]
    found: list[str] = []
    seen: set[str] = set()
    for entry in env.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        folder = os.path.normcase(os.path.abspath(entry.strip('"')))
        if folder in seen:
            continue
        seen.add(folder)
        for ext in exts:
            candidate = os.path.join(folder, name + ext)
            if os.path.isfile(candidate) and (windows or os.access(candidate, os.X_OK)):
                found.append(folder)
                break
    return found


def _display_dir(folder: str) -> str:
    home = os.path.normcase(os.path.expanduser("~"))
    return "~" + folder[len(home):] if folder.startswith(home) else folder


def _json_file(path: Path) -> tuple[dict | None, str | None]:
    if not path.exists():
        return None, "missing"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, "unreadable or malformed JSON"
    if not isinstance(data, dict):
        return None, "JSON root is not an object"
    return data, None


def diagnose(backup_root: Path, *, env: dict[str, str] | None = None) -> list[Check]:
    """Inspect local metadata only; never read or validate credential bytes."""
    env = dict(os.environ) if env is None else env
    checks = [Check("ccswap", "ok", __version__),
              _cli_check("claude"), _cli_check("codex")]
    for name in ("claude", "codex"):
        dirs = _install_dirs(name, env)
        if len(dirs) > 1:
            checks.append(Check(
                f"{name} installs", "warning",
                f"{len(dirs)} on PATH, first wins: "
                + ", ".join(_display_dir(d) for d in dirs),
            ))

    roster, roster_error = _json_file(backup_root / "sequence.json")
    identities: dict[str, tuple[str, str]] = {}
    if roster_error == "missing":
        checks.append(Check("accounts", "info", "no managed Claude accounts"))
    elif roster_error:
        checks.append(Check("accounts", "error", roster_error))
    else:
        accounts = roster.get("accounts")
        if not isinstance(accounts, dict) or not isinstance(roster.get("sequence"), list):
            checks.append(Check("accounts", "error", "invalid roster accounts"))
        else:
            seen: set[tuple[str, str]] = set()
            duplicates = 0
            for number, data in accounts.items():
                if not isinstance(data, dict):
                    continue
                identity = (str(data.get("email", "")),
                            str(data.get("organizationUuid", "") or ""))
                identities[str(number)] = identity
                if identity in seen:
                    duplicates += 1
                seen.add(identity)
            status = "warning" if duplicates else "ok"
            checks.append(Check("accounts", status,
                                f"{len(accounts)} managed, {duplicates} duplicate identities"))
            try:
                entries = UsageStore(backup_root / "cache").entries(identities)
                fresh = sum(
                    entry.last_good is not None and entry.age_s is not None
                    and entry.age_s <= STALE_OK_S
                    for entry in entries.values()
                )
                checks.append(Check(
                    "quota cache", "ok" if fresh == len(accounts) else "info",
                    f"{fresh}/{len(accounts)} snapshots under five minutes old",
                ))
            except (OSError, ValueError):
                checks.append(Check("quota cache", "warning", "unreadable"))

    settings, settings_error = _json_file(backup_root / "settings.json")
    settings_invalid = (
        settings is not None
        and (settings.get("schemaVersion", SETTINGS_SCHEMA_VERSION)
             != SETTINGS_SCHEMA_VERSION
             or ("autoswitch" in settings
                 and not isinstance(settings["autoswitch"], dict)))
    )
    checks.append(Check(
        "settings", "warning" if settings_invalid or
        (settings_error and settings_error != "missing") else "ok",
        "defaults" if settings_error == "missing" else
        (settings_error or ("unsupported schema or section" if settings_invalid
                            else "valid JSON")),
    ))
    mappings, mappings_error = _json_file(backup_root / "mappings.json")
    if mappings_error and mappings_error != "missing":
        checks.append(Check("mappings", "warning", mappings_error))
    elif mappings is not None:
        rows = mappings.get("mappings")
        if mappings.get("schemaVersion", 1) != 1 or not isinstance(rows, dict):
            checks.append(Check("mappings", "warning", "unsupported schema or section"))
            rows = None
        known = set(identities.values())
        orphaned = sum(
            (str(row.get("email", "")), str(row.get("organizationUuid", "") or ""))
            not in known
            for row in rows.values() if isinstance(row, dict)
        ) if isinstance(rows, dict) else 0
        if rows is not None:
            checks.append(Check("mappings", "warning" if orphaned else "ok",
                                f"{orphaned} mappings to missing accounts"))

    for variable in ("ANTHROPIC_BASE_URL", "OPENAI_BASE_URL"):
        if env.get(variable):
            checks.append(Check(variable, "warning",
                                f"custom endpoint {_endpoint(env[variable])}"))
    for variable in ("HTTPS_PROXY", "HTTP_PROXY"):
        if env.get(variable) or env.get(variable.lower()):
            value = env.get(variable) or env[variable.lower()]
            checks.append(Check(variable, "info",
                                f"proxy configured: {_endpoint(value)}"))
    for variable in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
        if env.get(variable):
            checks.append(Check(variable, "warning", "auth override is set"))
    if env.get("CLAUDE_CONFIG_DIR"):
        checks.append(Check("CLAUDE_CONFIG_DIR", "info",
                            "custom Claude profile is active"))

    if sys.platform == "win32":
        checks.append(Check("credential ACL", "info",
                            "Windows ACL not verified by this command"))
    elif backup_root.exists():
        try:
            mode = stat.S_IMODE(backup_root.stat().st_mode)
            checks.append(Check("credential directory", "warning" if mode & 0o077 else "ok",
                                f"mode {mode:04o}"))
        except OSError:
            checks.append(Check("credential directory", "warning", "mode unreadable"))
    return checks
