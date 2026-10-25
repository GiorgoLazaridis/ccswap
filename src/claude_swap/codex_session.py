"""Internal Codex session custody primitives (no launch integration)."""

from __future__ import annotations

import base64
import ctypes
import errno
import hashlib
import json
import os
import re
import stat
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from claude_swap.exceptions import SessionError
from claude_swap.fsutil import atomic_write_text
from claude_swap.session import slugify_email


def _require(condition: bool) -> None:
    if not condition:
        raise SessionError("Malformed session data.")


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "\x00" not in value


def _decimal(value: object, *, positive: bool = False) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9]+", value) is not None and (
        not positive or any(character != "0" for character in value)
    )


def _absolute(value: object) -> bool:
    return _text(value) and os.path.isabs(value) and os.path.normpath(value) == value


def _hex(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _object(value: object, fields: tuple[str, ...]) -> dict[str, Any]:
    _require(isinstance(value, dict) and set(fields) <= value.keys())
    return value


def validate_identity(value: object) -> dict[str, Any]:
    """Validate the normalized identity stored in session coordination records."""
    data = _object(value, ("userId", "workspaceId", "email", "loginOwnerId"))
    _require(all(_text(data[field]) for field in ("userId", "workspaceId", "email")))
    _require(all(data[field] == data[field].strip() for field in ("userId", "workspaceId")))
    _require(data["email"] == data["email"].strip().casefold())
    owner = data["loginOwnerId"]
    _require(owner is None or (_text(owner) and owner == owner.strip()))
    return data


def same_identity(left: object, right: object) -> bool:
    """Compare custody, allowing legacy absence of the optional login owner."""
    left_identity = validate_identity(left)
    right_identity = validate_identity(right)
    return all(
        left_identity[field] == right_identity[field]
        for field in ("userId", "workspaceId")
    ) and (
        left_identity["loginOwnerId"] is None
        or right_identity["loginOwnerId"] is None
        or left_identity["loginOwnerId"] == right_identity["loginOwnerId"]
    )


def _json(text: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = dict(items)
        _require(len(result) == len(items))
        return result

    return json.loads(text, object_pairs_hook=pairs, parse_constant=lambda _: _require(False))


def extract_identity(auth: object) -> tuple[dict[str, Any], str | None]:
    """Decode local labels, without claiming JWT signature verification."""
    try:
        data = _object(auth, ("tokens",))
        _require(data.get("auth_mode") in (None, "chatgpt"))
        _require(all(
            data.get(field) is None
            for field in (
                "OPENAI_API_KEY",
                "personal_access_token",
                "agent_identity",
                "bedrock_api_key",
                "bedrock_access_keys",
            )
        ))
        tokens = _object(
            data["tokens"], ("id_token", "access_token", "refresh_token", "account_id"),
        )
        _require(all(
            _text(tokens[field]) for field in ("id_token", "access_token", "refresh_token")
        ))
        if "last_refresh" in data:
            timestamp = data["last_refresh"]
            _require(isinstance(timestamp, str) and bool(re.fullmatch(
                r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
                r"(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})", timestamp,
            )))
            datetime.fromisoformat(timestamp)
        if "oauth_provenance" in tokens:
            provenance = _object(tokens["oauth_provenance"], ("issuer", "client_id"))
            _require(all(isinstance(provenance[field], str) for field in ("issuer", "client_id")))
        pieces = tokens["id_token"].split(".")
        _require(len(pieces) == 3 and all(
            re.fullmatch(r"[A-Za-z0-9_-]+", piece) for piece in pieces
        ))
        decoded_payload = base64.b64decode(
            pieces[1] + "=" * (-len(pieces[1]) % 4),
            altchars=b"-_",
            validate=True,
        )
        _require(base64.urlsafe_b64encode(decoded_payload).decode().rstrip("=") == pieces[1])
        claims = _object(_json(decoded_payload.decode("utf-8")), ("https://api.openai.com/auth",))
        nested = _object(
            claims["https://api.openai.com/auth"],
            ("chatgpt_account_id",),
        )
        _require(nested["chatgpt_account_id"] == tokens["account_id"])
        user_id = nested.get("chatgpt_user_id")
        fallback_user = nested.get("user_id")
        _require(user_id is None or fallback_user is None or user_id == fallback_user)
        profile = claims.get("https://api.openai.com/profile")
        profile_email = _object(profile, ()).get("email") if profile is not None else None
        email = claims.get("email")
        _require(email is None or _text(email))
        _require(profile_email is None or _text(profile_email))
        _require(email is None or profile_email is None
                 or email.strip().casefold() == profile_email.strip().casefold())
        email = email if email is not None else profile_email
        _require(_text(email))
        identity = validate_identity(dict(
            userId=user_id if user_id is not None else fallback_user,
            workspaceId=nested["chatgpt_account_id"],
            email=email.strip().casefold(),
            loginOwnerId=tokens.get("login_owner_id"),
        ))
        plan = nested.get("chatgpt_plan_type")
        _require(plan is None or _text(plan))
        return identity, plan
    except (ValueError, TypeError, UnicodeError, RecursionError, SessionError):
        raise SessionError("Invalid ChatGPT session identity.") from None


def session_plan_supported(plan: object) -> bool:
    """Accept the known complement of the plans that receive cloud-managed configuration."""
    return isinstance(plan, str) and plan in {
        "free",
        "go",
        "plus",
        "pro",
        "prolite",
        "promax",
        "team",
        "self_serve_business_prolite",
        "self_serve_business_usage_based",
    }


def auth_fingerprint(auth: object) -> str:
    """Hash the complete semantic document; never discard unknown auth fields."""
    try:
        _require(isinstance(auth, dict))
        encoded = json.dumps(
            auth,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise SessionError("Malformed auth document.") from None


def _process(value: object) -> None:
    data = _object(value, ("pid", "start"))
    _require(type(data["pid"]) is int and 0 < data["pid"] <= 2**32 - 1)
    start = _object(data["start"], ("kind",))
    kind = start["kind"]
    _require(kind in ("linux", "macos", "windows"))
    field = {"linux": "ticks", "macos": "uniqueId", "windows": "creationTime100ns"}[kind]
    _require(_decimal(start.get(field)))
    if kind != "windows":
        _require(_text(start.get("bootId")))


def validate_record(value: object, kind: str, *, run_id: str | None = None) -> dict[str, Any]:
    """Validate schema structure; filesystem ownership/alias checks belong to transactions."""
    data = _object(value, ("schemaVersion", "home"))
    _require(type(data["schemaVersion"]) is int and data["schemaVersion"] == 1)
    _require(_absolute(data["home"]))
    if kind in ("manifest", "stale"):
        _object(data, ("accountNumber", "identity"))
        _require(_decimal(data["accountNumber"], positive=True)
                 and not data["accountNumber"].startswith("0"))
        validate_identity(data["identity"])
        if kind == "stale":
            _object(data, ("oldFingerprint", "newFingerprint", "reason"))
            _require(_hex(data["oldFingerprint"]) and _hex(data["newFingerprint"]))
            _require(data["reason"] in ("replacement", "refresh"))
        else:
            _object(data, ("sourceHome", "baselineFingerprint", "bootstrapState", "sharedItems"))
            _require(_absolute(data["sourceHome"]) and _hex(data["baselineFingerprint"]))
            _require(data["bootstrapState"] in ("ready", "preparing"))
            _require(isinstance(data["sharedItems"], dict))
            for destination, item in data["sharedItems"].items():
                _require(
                    _text(destination)
                    and not os.path.isabs(destination)
                    and "\\" not in destination
                    and all(part not in ("", ".", "..") for part in destination.split("/"))
                )
                _require(destination in (
                    "config.toml", "AGENTS.md", "AGENTS.override.md", "rules", "prompts",
                ) or (destination.startswith("skills/") and destination.count("/") == 1
                      and destination != "skills/.system"))
                _object(item, ("source", "mode"))
                _require(_absolute(item["source"]) and item["mode"] in ("symlink", "copy"))
    elif kind == "run":
        _object(data, ("runId", "supervisor", "child", "phase", "shared", "jobName", "exitCode"))
        try:
            run_uuid = UUID(data["runId"])
            _require(run_uuid.version == 4 and str(run_uuid) == data["runId"] == run_id)
        except (ValueError, TypeError, AttributeError):
            raise SessionError("Malformed session data.") from None
        _process(data["supervisor"])
        if data["child"] is not None:
            _process(data["child"])
        _require(data["phase"] in ("starting", "running", "waiting", "finished"))
        _require(type(data["shared"]) is bool)
        exit_code = data["exitCode"]
        _require(exit_code is None or (
            type(exit_code) is int and -(2**31) <= exit_code <= 2**32 - 1
        ))
        windows = data["supervisor"]["start"]["kind"] == "windows"
        _require(data["jobName"] == ("Local\\ccswap-codex-" + run_id if windows else None))
    else:
        raise SessionError("Unknown session record kind.")
    return data


def load_record(path: Path, kind: str) -> dict[str, Any] | None:
    """Read only: missing returns None; unreadable and malformed remain distinct errors."""
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        if not hasattr(os, "O_NOFOLLOW"):
            _require(not path.is_symlink())
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as error:
        if error.errno == errno.ELOOP or path.is_dir() or path.is_symlink():
            raise SessionError("Malformed session record.") from None
        raise SessionError("Unreadable session record.") from None
    try:
        _require(stat.S_ISREG(os.fstat(descriptor).st_mode))
        with os.fdopen(descriptor, "r", encoding="utf-8", closefd=False) as stream:
            return validate_record(_json(stream.read()), kind, run_id=path.stem)
    except OSError:
        raise SessionError("Unreadable session record.") from None
    except (ValueError, TypeError, UnicodeError, RecursionError, SessionError):
        raise SessionError("Malformed session record.") from None
    finally:
        os.close(descriptor)


def _ordinary(path: Path, *, directory: bool = False) -> bool:
    """Reject links (including Windows reparse points), not just their targets."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    _require(not stat.S_ISLNK(info.st_mode)
             and not getattr(info, "st_file_attributes", 0) & 0x400)
    _require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
    return True


def _checked_parents(path: Path, tree: Path) -> None:
    _require(path.is_relative_to(tree) and ".." not in path.relative_to(tree).parts)
    _require(_ordinary(tree, directory=True))
    for part in path.relative_to(tree).parts:
        tree = tree / part
        _require(_ordinary(tree, directory=True))


def _private_directory(path: Path, tree: Path) -> bool:
    _checked_parents(path.parent, tree)
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        _require(_ordinary(path, directory=True))
        return False
    if os.name == "posix":
        path.chmod(0o700)
    return True


def _session_write(tree: Path, path: Path, data: dict[str, Any]) -> None:
    """Write only ordinary files below the checked, wrapper-owned tree."""
    _require(path.is_relative_to(tree) and path != tree
             and ".." not in path.relative_to(tree).parts)
    _checked_parents(path.parent, tree)
    _ordinary(path)
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, allow_nan=False),
                      mode=0o600, follow_symlinks=False)


def _publish_home(stage: Path, home: Path) -> None:
    """Atomic no-replace publication; no check-then-rename POSIX fallback."""
    if os.name == "nt":
        os.rename(stage, home)  # Windows rename refuses an existing destination.
        return
    # Darwin's ABI/behaviour remains unverified until native CI passes.
    darwin = sys.platform == "darwin"
    _require(darwin or sys.platform.startswith("linux"))
    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib" if darwin else None, use_errno=True)
    rename = getattr(library, "renamex_np" if darwin else "renameat2", None)
    _require(rename is not None)
    rename.argtypes = ([ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint] if darwin else
                      [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
                       ctypes.c_uint])
    rename.restype = ctypes.c_int
    arguments = ((os.fsencode(stage), os.fsencode(home), 0x00000004) if darwin else
                 (-100, os.fsencode(stage), -100, os.fsencode(home), 1))
    if rename(*arguments):
        raise OSError(ctypes.get_errno(), "Session publication refused.")


_BOOTSTRAP_DIRS = ("skills", "sessions", "archived_sessions", "log")
_BOOTSTRAP_FILES = ("auth.json", ".ccswap-session.json")


def _new_manifest(home: Path, source: Path, number: str, auth: dict[str, Any]) -> dict[str, Any]:
    identity, _ = extract_identity(auth)
    return validate_record(dict(
        schemaVersion=1, accountNumber=number, identity=identity,
        home=str(home), sourceHome=str(source), baselineFingerprint=auth_fingerprint(auth),
        bootstrapState="preparing", sharedItems={},
    ), "manifest")


def _owned(path: Path) -> None:
    if os.name == "posix":
        _require(path.lstat().st_uid == os.geteuid())


def _resume_stage(stage: Path, manifest: dict[str, Any]) -> None:
    """Validate the entire allowlist before removing any writer leftovers."""
    _require(_ordinary(stage, directory=True))
    _owned(stage)
    entries = list(stage.iterdir())
    if not entries:
        return
    receipt = stage / ".ccswap-session.json"
    _require(_ordinary(receipt))
    previous = load_record(receipt, "manifest")
    _require(previous is not None and all(
        previous[field] == manifest[field]
        for field in ("accountNumber", "identity", "home", "sourceHome")
    ))
    leftovers = []
    for entry in entries:
        _owned(entry)
        if entry.name in _BOOTSTRAP_DIRS:
            _require(_ordinary(entry, directory=True) and not any(entry.iterdir()))
        else:
            _require(_ordinary(entry))
            if entry.name not in _BOOTSTRAP_FILES:
                # fsutil: <target[:40]>.<8 tempfile characters>.tmp.
                _require(any(re.fullmatch(re.escape(name[:40]) + r"\.[a-z0-9_]{8}\.tmp",
                                          entry.name) for name in _BOOTSTRAP_FILES))
                leftovers.append(entry)
    for entry in leftovers:
        entry.unlink()


def _sync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        _require(stat.S_ISDIR(os.fstat(descriptor).st_mode))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def bootstrap_home(
    backup_root: Path, source_home: Path, number: str, auth: dict[str, Any],
) -> Path:
    """Bootstrap only a new home; caller holds R/.lock. Never scan or launch.

    A matching manifest is the staging ownership receipt; an empty stage can
    also be resumed. Other stages without a receipt are retained. Existing
    final homes belong to reconciliation, never to this bootstrap operation.
    A7 permits path-based checks: a hostile same-uid process can still swap
    checked paths between validation and mutation; that adversary is out of scope.
    """
    stage = None
    try:
        _require(_decimal(number, positive=True) and not number.startswith("0"))
        identity, _ = extract_identity(auth)
        _require(_absolute(str(backup_root)) and _absolute(str(source_home)))
        backup_root = backup_root.resolve(strict=True)
        source_home = source_home.resolve(strict=True)
        _require(_ordinary(backup_root, directory=True))
        _require(_ordinary(source_home, directory=True))
        sessions = backup_root / "codex" / "sessions"
        home = sessions / f"{number}-{slugify_email(identity['email'])}"
        stage = sessions / f".{home.name}.preparing"
        for destination in (home, stage):
            _require(not source_home.is_relative_to(destination)
                     and not destination.is_relative_to(source_home))
        manifest = _new_manifest(home, source_home, number, auth)
        _private_directory(sessions.parent, backup_root)
        _private_directory(sessions, backup_root)
        _require(not os.path.lexists(home))
        if not _private_directory(stage, backup_root):
            _resume_stage(stage, manifest)
        _session_write(backup_root, stage / ".ccswap-session.json", manifest)
        # Codex creates databases, caches and runtime coordination itself.
        for name in _BOOTSTRAP_DIRS:
            _private_directory(stage / name, backup_root)
        _session_write(backup_root, stage / "auth.json", auth)
        manifest["bootstrapState"] = "ready"
        _session_write(backup_root, stage / ".ccswap-session.json", manifest)
        _checked_parents(stage, backup_root)
        _sync_directory(stage)
        _publish_home(stage, home)
        _sync_directory(sessions)
        return home
    except (OSError, ValueError, TypeError, RecursionError, RuntimeError, SessionError) as error:
        location = f" at {stage}" if stage is not None else ""
        detail = ""
        if isinstance(error, OSError):
            detail = f" ({errno.errorcode.get(error.errno, 'UNKNOWN')})"
        raise SessionError(
            f"Session bootstrap refused{location}{detail}; existing evidence retained."
        ) from None
