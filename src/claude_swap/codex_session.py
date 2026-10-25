"""Strict, read-only primitives for Codex session custody (no launch integration)."""

from __future__ import annotations

import base64
import errno
import hashlib
import json
import os
import re
import stat
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from claude_swap.exceptions import SessionError


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
