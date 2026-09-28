"""One-line account status for Claude Code's ``statusLine`` command.

Strictly read-only: it renders the quota cache that ``ccswap auto``/``list``
already maintain and never fetches usage, refreshes a token, or writes a
file. Claude Code pipes a session JSON on stdin; its native ``rate_limits``
(the running session's own account) replace the cached active-account
values. Codex quota comes from the snapshot the ``ccswap codex auto`` loop
writes each tick.
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import time
from collections.abc import Sequence
from typing import TextIO

from claude_swap.models import AccountSnapshot, AccountsSnapshot
from claude_swap.oauth import relevant_windows
from claude_swap.poll_policy import parse_reset_ts

_GREEN, _YELLOW, _RED, _DIM, _RESET = (
    "\x1b[32m", "\x1b[33m", "\x1b[31m", "\x1b[2m", "\x1b[0m",
)
_ORANGE = "\x1b[38;5;208m"
_URGENCY = {"green": _GREEN, "orange": _ORANGE, "red": _RED}
# Context colors switch at the same points as ccusage's statusline.
CONTEXT_WARN_PCT, CONTEXT_HIGH_PCT = 50.0, 80.0
_SEP = " │ "  # │
# `ccswap codex auto` refreshes its snapshot every minute; older than this,
# the loop is not running and the numbers are marked stale.
CODEX_STALE_S = 15 * 60


def read_session(stream: TextIO) -> dict | None:
    """The JSON Claude Code pipes in, or None when run by hand (a TTY)."""
    try:
        if stream is None or stream.isatty():
            return None
        data = json.loads(stream.read() or "null")
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _num(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _countdown(reset_ts: float | None, now: float) -> str:
    if reset_ts is None or reset_ts <= now:
        return ""
    minutes = int((reset_ts - now) // 60)
    days, rest = divmod(minutes, 24 * 60)
    hours, mins = divmod(rest, 60)
    if days:
        return f"{days}d{hours}h"
    if hours:
        return f"{hours}h{mins:02d}m"
    return f"{mins}m"


def _paint(text: str, pct: float, color: bool) -> str:
    if not color:
        return text
    tone = _GREEN if pct < 70 else _YELLOW if pct < 90 else _RED
    return f"{tone}{text}{_RESET}"


def _label(account: AccountSnapshot) -> str:
    name = account.alias or account.email.split("@", 1)[0]
    return f"#{account.number} {name}".rstrip()


def _cached_windows(
    account: AccountSnapshot, models: Sequence[str]
) -> list[tuple[str, float, float | None]]:
    """``(label, pct, reset_ts)`` from the last good measurement."""
    return [
        (label, pct, parse_reset_ts(reset))
        for label, pct, reset in relevant_windows(account.usage.last_good, models)
    ]


def _session_windows(session: dict | None) -> list[tuple[str, float, float | None]]:
    limits = session.get("rate_limits") if session else None
    if not isinstance(limits, dict):
        return []
    windows = []
    for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
        window = limits.get(key)
        if not isinstance(window, dict):
            continue
        pct = _num(window.get("used_percentage"))
        if pct is not None:
            windows.append((label, pct, _num(window.get("resets_at"))))
    return windows


def _reserve(number: str, windows: list[tuple[str, float, float | None]],
             now: float, color: bool) -> str:
    """One reserve account: its binding window, plus when it frees up."""
    if not windows:
        return f"#{number} ?"
    label, pct, reset = max(windows, key=lambda w: w[1])
    text = f"#{number} {label} {pct:.0f}%"
    if pct >= 90 and (left := _countdown(reset, now)):
        text += f" reset {left}"
    return _paint(text, pct, color)


def _dim(text: str, color: bool) -> str:
    return f"{_DIM}{text}{_RESET}" if color else text


def _claude_part(snapshot: AccountsSnapshot, session: dict | None,
                 models: Sequence[str], now: float, color: bool) -> str:
    active = next(
        (a for a in snapshot.accounts if a.number == snapshot.active_number), None
    )
    if active is None:
        head = "Claude: no active account"
    else:
        # Claude Code's own numbers are fresher for the running session;
        # any window it did not send (or only ccswap reports, like per-model
        # limits) comes from the cache.
        fresh = {w[0]: w for w in _session_windows(session)}
        windows = [fresh.pop(w[0], w) for w in _cached_windows(active, models)]
        windows += fresh.values()
        shown = " ".join(
            _paint(f"{label} {pct:.0f}%", pct, color) for label, pct, _ in windows
        ) or (active.usage.sentinel or "usage ?")
        head = f"Claude {_label(active)} {shown}"

    others = []
    for account in snapshot.accounts:
        if account is active or account.disabled or account.kind != "oauth":
            continue
        # Old cached percentages must not advertise an account that cannot
        # take over (missing credentials, dead token, re-login needed).
        if not account.switchable or account.usage.sentinel:
            state = account.usage.sentinel or "no credentials"
            others.append(_dim(f"#{account.number} {state}", color))
            continue
        others.append(_reserve(account.number, _cached_windows(account, models),
                               now, color))
    return " · ".join([head, " ".join(others)]) if others else head


def _reset_badge(usage: object, now: float, color: bool) -> str:
    """``R3`` colored by how soon the next banked reset must be redeemed;
    ``!`` when redeeming now buys real headroom (see reset_advice)."""
    from claude_swap.reset_advice import advise

    advice = advise(usage, now)
    if advice is None:
        return ""
    text = f"R{advice.count}" + ("!" if advice.worth_now else "")
    return f"{_URGENCY[advice.urgency]}{text}{_RESET}" if color else text


def _session_line(session: dict | None, color: bool) -> str | None:
    """``🤖 Opus 5.5 | 🧠 400,000 (40%)`` from Claude Code's own session JSON."""
    if not session:
        return None
    model = session.get("model")
    name = model.get("display_name") if isinstance(model, dict) else None
    context = session.get("context_window")
    pct = tokens = None
    if isinstance(context, dict):
        pct = _num(context.get("used_percentage"))
        current = context.get("current_usage")
        if isinstance(current, dict):
            counts = [_num(current.get(key)) for key in (
                "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")]
            if any(count is not None for count in counts):
                tokens = sum(count for count in counts if count is not None)
        size = _num(context.get("context_window_size"))
        if tokens is None and pct is not None and size:
            tokens = pct * size / 100
    segments = []
    if isinstance(name, str) and name:
        segments.append(f"\U0001f916 {name}")
    if tokens is not None or pct is not None:
        text = f"{tokens:,.0f}" if tokens is not None else ""
        if pct is not None:
            text = f"{text} ({pct:.0f}%)" if text else f"{pct:.0f}%"
            if color:
                tone = (_GREEN if pct < CONTEXT_WARN_PCT else
                        _YELLOW if pct < CONTEXT_HIGH_PCT else _RED)
                text = f"{tone}{text}{_RESET}"
        segments.append(f"\U0001f9e0 {text}")
    return " | ".join(segments) or None


def _codex_windows(usage: object) -> list[tuple[str, float, float | None]]:
    return [
        ("7d" if label == "Weekly" else label, pct, parse_reset_ts(reset))
        for label, pct, reset in relevant_windows(usage if isinstance(usage, dict) else None)
    ]


def _codex_part(codex: dict, now: float, color: bool) -> str | None:
    """Codex pool from the snapshot ``ccswap codex auto`` keeps current."""
    accounts = codex.get("accounts") or {}
    active = codex.get("active")
    rows = {
        str(n): row for n, row in accounts.items()
        if isinstance(row, dict) and row.get("kind") == "oauth"
    }

    def note(row: dict) -> str:
        """Why a row's numbers must not be read as current, or ''."""
        if row.get("error"):
            return "fetch error"
        fetched = _num(row.get("fetchedAt"))
        if fetched is None:
            return "no data"
        if now - fetched > CODEX_STALE_S:
            return f"{int((now - fetched) // 60)}m old"
        return ""

    parts = []
    if isinstance(active, str) and active in rows:
        # The active login stays visible even when held out of rotation.
        row = rows[active]
        windows = _codex_windows(row.get("usage"))
        shown = " ".join(
            _paint(f"{label} {pct:.0f}%", pct, color) for label, pct, _ in windows
        ) or "usage ?"
        head = f"Codex #{active} {shown}"
        if badge := _reset_badge(row.get("usage"), now, color):
            head += f" {badge}"
        if why := note(row):
            head += " " + _dim(f"({why})", color)
        parts.append(head)
    else:
        parts.append("Codex")
    others = []
    for n, row in sorted(rows.items(), key=lambda item: int(item[0])):
        if n == active or row.get("disabled"):
            continue
        if not row.get("usable", True) or row.get("error"):
            # auto-switch would not pick it; do not advertise old numbers
            state = "unusable" if not row.get("usable", True) else "fetch error"
            others.append(_dim(f"#{n} {state}", color))
            continue
        text = _reserve(n, _codex_windows(row.get("usage")), now, color)
        if badge := _reset_badge(row.get("usage"), now, color):
            text += f" {badge}"
        if why := note(row):
            text += " " + _dim(f"({why})", color)
        others.append(text)
    if others:
        parts.append(" ".join(others))
    if len(parts) == 1 and parts[0] == "Codex":
        return None
    part = " · ".join(parts)
    taken = _num(codex.get("takenAt"))
    if taken is None or now - taken > CODEX_STALE_S:
        age = "" if taken is None else f" {int((now - taken) // 60)}m"
        part += " " + _dim(f"(stale{age})", color)
    return part


def render(
    snapshot: AccountsSnapshot,
    session: dict | None = None,
    *,
    models: Sequence[str] = (),
    now: float | None = None,
    color: bool = True,
    codex: dict | None = None,
) -> str:
    now = time.time() if now is None else now
    parts = [_claude_part(snapshot, session, models, now, color)]
    if codex and (codex_part := _codex_part(codex, now, color)):
        parts.append(codex_part)
    pool = _SEP.join(parts)
    # Two lines like claude-hud: the running session first, the account pool below.
    first = _session_line(session, color)
    return f"{first}\n{pool}" if first else pool


def main(argv: list[str]) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="ccswap statusline",
        description="Print one status line for Claude Code (read-only, cached data).",
    )
    parser.add_argument("--no-color", action="store_true", help="Plain text output")
    args = parser.parse_args(argv)
    # Read-only means no log file either: a failing snapshot would otherwise
    # create or rotate claude-swap.log from inside every prompt refresh.
    logging.disable(logging.CRITICAL)
    session = read_session(sys.stdin)
    try:
        from claude_swap.settings import load_settings, parse_model_names
        from claude_swap.switcher import ClaudeAccountSwitcher

        switcher = ClaudeAccountSwitcher(read_only=True)
        snapshot = switcher.accounts_snapshot(fetch=set())
        models = parse_model_names(load_settings(switcher.backup_dir).model)
        from claude_swap.codex import load_usage_snapshot

        line = render(
            snapshot, session, models=models,
            color=not args.no_color and "NO_COLOR" not in os.environ,
            codex=load_usage_snapshot(switcher.backup_dir),
        )
    except Exception as exc:  # a status line must never break the prompt
        line = f"ccswap: {type(exc).__name__}"
    sys.stdout.buffer.write(line.encode("utf-8") + b"\n")
    sys.stdout.flush()
