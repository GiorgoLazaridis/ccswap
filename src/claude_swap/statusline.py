"""One-line account status for Claude Code's ``statusLine`` command.

Strictly read-only: it renders the quota cache that ``ccswap auto``/``list``
already maintain and never fetches usage, refreshes a token, or writes a
file. Claude Code pipes a session JSON on stdin; when present, its native
``rate_limits`` (the running session's own account) and context/cost fields
are shown next to the pool state that only ccswap knows.
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
_SEP = " │ "  # │


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


def render(
    snapshot: AccountsSnapshot,
    session: dict | None = None,
    *,
    models: Sequence[str] = (),
    now: float | None = None,
    color: bool = True,
) -> str:
    now = time.time() if now is None else now
    active = next(
        (a for a in snapshot.accounts if a.number == snapshot.active_number), None
    )
    parts: list[str] = []
    if active is None:
        parts.append("ccswap: no active account")
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
        parts.append(f"{_label(active)} {shown}")

    others = []
    for account in snapshot.accounts:
        if account is active or account.disabled or account.kind != "oauth":
            continue
        # Old cached percentages must not advertise an account that cannot
        # take over (missing credentials, dead token, re-login needed).
        if not account.switchable or account.usage.sentinel:
            state = account.usage.sentinel or "no credentials"
            others.append(f"{_DIM}#{account.number} {state}{_RESET}" if color
                          else f"#{account.number} {state}")
            continue
        windows = _cached_windows(account, models)
        if not windows:
            others.append(f"#{account.number} ?")
            continue
        label, pct, reset = max(windows, key=lambda w: w[1])
        text = f"#{account.number} {label} {pct:.0f}%"
        if pct >= 90 and (left := _countdown(reset, now)):
            text += f" ↻{left}"  # ↻
        others.append(_paint(text, pct, color))
    if others:
        parts.append(" ".join(others))

    extras = []
    context = session.get("context_window") if session else None
    used = _num(context.get("used_percentage")) if isinstance(context, dict) else None
    if used is not None:
        extras.append(f"ctx {used:.0f}%")
    cost = session.get("cost") if session else None
    usd = _num(cost.get("total_cost_usd")) if isinstance(cost, dict) else None
    if usd is not None:
        extras.append(f"${usd:.2f}")
    if extras:
        text = " ".join(extras)
        parts.append(f"{_DIM}{text}{_RESET}" if color else text)
    return _SEP.join(parts)


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
        line = render(
            snapshot, session, models=models,
            color=not args.no_color and "NO_COLOR" not in os.environ,
        )
    except Exception as exc:  # a status line must never break the prompt
        line = f"ccswap: {type(exc).__name__}"
    sys.stdout.buffer.write(line.encode("utf-8") + b"\n")
    sys.stdout.flush()
