"""Explainable allocation of a new native Claude Code session.

This is deliberately separate from auto-switching: evaluating a plan never
changes the default login, refreshes a token, or starts a provider process.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from claude_swap.models import AccountSnapshot, AccountsSnapshot
from claude_swap.poll_policy import parse_reset_ts
from claude_swap.session import scan_live_sessions, session_dir_for
from claude_swap.usage_store import STALE_OK_S


@dataclass(frozen=True)
class Candidate:
    number: str
    email: str
    alias: str
    five_hour_used: float | None
    weekly_used: float | None
    age_s: float | None
    sessions: int
    reasons: tuple[str, ...]
    skipped: str | None = None
    mapped: bool = False

    @property
    def headroom(self) -> float:
        assert self.five_hour_used is not None
        assert self.weekly_used is not None
        return 100.0 - max(self.five_hour_used, self.weekly_used)


@dataclass(frozen=True)
class AllocationPlan:
    selected: str | None
    candidates: tuple[Candidate, ...]
    mapped_account: str | None
    error: str | None
    window_advice: WindowAdvice | None = None


@dataclass(frozen=True)
class WindowAdvice:
    account: str
    ideal_start_ts: float
    observed_windows: int


def _percentage(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    pct = float(value)
    return pct if 0.0 <= pct <= 100.0 else None


def _window_pct(usage: dict, key: str) -> float | None:
    window = usage.get(key)
    return _percentage(window.get("pct")) if isinstance(window, dict) else None


def _candidate(
    account: AccountSnapshot,
    *,
    active_number: str | None,
    backup_dir: Path,
    mapped: bool,
) -> Candidate:
    reasons: list[str] = []
    skipped: str | None = None
    sessions = 0
    value = account.usage.decision_value()
    five = _window_pct(value, "five_hour") if isinstance(value, dict) else None
    weekly = _window_pct(value, "seven_day") if isinstance(value, dict) else None
    age = account.usage.age_s

    if account.number == active_number:
        skipped = "active default login cannot be pinned in an isolated profile"
    elif account.kind != "oauth":
        skipped = "API-key sessions are not supported"
    elif not account.switchable:
        skipped = "stored credentials unavailable"
    elif account.disabled and not mapped:
        skipped = "disabled for automatic selection"
    elif five is None or weekly is None or age is None or age > STALE_OK_S:
        skipped = "5h/7d usage unknown or older than five minutes"
    elif max(five, weekly) >= 100.0:
        skipped = "quota window exhausted"

    if skipped is None:
        session_dir = session_dir_for(backup_dir, account.number, account.email)
        live, unreadable = scan_live_sessions(session_dir)
        if unreadable:
            skipped = "session process state unreadable"
        else:
            sessions = len(live)
            reasons.append(f"5h headroom {100.0 - five:.0f}%")
            reasons.append(f"7d headroom {100.0 - weekly:.0f}%")
            reasons.append(f"{sessions} active sessions")
            reasons.append(f"quota snapshot {age:.0f}s old")
            if mapped:
                reasons.append("hard project mapping")

    return Candidate(
        number=account.number,
        email=account.email,
        alias=account.alias,
        five_hour_used=five,
        weekly_used=weekly,
        age_s=age,
        sessions=sessions,
        reasons=tuple(reasons),
        skipped=skipped,
        mapped=mapped,
    )


def allocate(
    snapshot: AccountsSnapshot,
    *,
    backup_dir: Path,
    mapped_account: str | None = None,
    missing_mapping: str | None = None,
) -> AllocationPlan:
    """Rank eligible profiles from one coherent snapshot, without side effects.

    Headroom is the binding 5h/7d minimum. Among accounts within ten
    percentage points of the best, prefer fewer sessions. Ten points is a
    deliberately coarse similarity band, matching the auto-switch default
    hysteresis rather than pretending that one point predicts session cost.
    """
    candidates = tuple(
        _candidate(
            account,
            active_number=snapshot.active_number,
            backup_dir=backup_dir,
            mapped=account.number == mapped_account,
        )
        for account in snapshot.accounts
    )
    advice = _window_advice(snapshot, candidates)
    if missing_mapping is not None:
        return AllocationPlan(None, candidates, None,
                              f"Mapped account {missing_mapping} no longer exists",
                              advice)
    if mapped_account is not None:
        mapped = next((c for c in candidates if c.number == mapped_account), None)
        if mapped is None:
            return AllocationPlan(None, candidates, mapped_account,
                                  "Mapped account disappeared during selection", advice)
        if mapped.skipped:
            return AllocationPlan(None, candidates, mapped_account,
                                  f"Mapped account {mapped.number}: {mapped.skipped}",
                                  advice)
        return AllocationPlan(mapped.number, candidates, mapped_account, None, advice)

    eligible = [c for c in candidates if c.skipped is None]
    if not eligible:
        return AllocationPlan(None, candidates, None,
                              "No safe isolated account has fresh 5h/7d usage",
                              advice)
    best_headroom = max(c.headroom for c in eligible)
    comparable = [c for c in eligible if best_headroom - c.headroom <= 10.0]
    winner = min(comparable, key=lambda c: (c.sessions, -c.headroom, int(c.number)))
    return AllocationPlan(winner.number, candidates, None, None, advice)


def _window_advice(
    snapshot: AccountsSnapshot, candidates: tuple[Candidate, ...]
) -> WindowAdvice | None:
    """Suggest a natural start in the largest observed 5h gap.

    A reset alone does not prove a window start. Only a current, nonzero 5h
    measurement with a plausible reset contributes. No request is generated.
    """
    period = 5 * 60 * 60
    now = snapshot.taken_at
    phases: list[float] = []
    dormant: list[Candidate] = []
    for account, candidate in zip(snapshot.accounts, candidates):
        if candidate.age_s is None or candidate.age_s > STALE_OK_S:
            continue
        value = account.usage.decision_value()
        if not isinstance(value, dict):
            continue
        window = value.get("five_hour")
        if not isinstance(window, dict):
            continue
        reset = parse_reset_ts(window.get("resets_at"))
        if candidate.skipped is None and candidate.five_hour_used == 0 and reset is None:
            dormant.append(candidate)
        elif (candidate.five_hour_used is not None
              and candidate.five_hour_used > 0
              and reset is not None and now < reset <= now + period):
            phases.append((reset - period) % period)
    if not phases or not dormant:
        return None
    phases.sort()
    gaps = [
        (phases[i],
         (phases[i + 1] if i + 1 < len(phases) else phases[0] + period)
         - phases[i])
        for i in range(len(phases))
    ]
    gap_start, gap_size = max(gaps, key=lambda gap: gap[1])
    midpoint = (gap_start + gap_size / 2) % period
    start = now - now % period + midpoint
    if start < now:
        start += period
    target = min(dormant, key=lambda c: (c.sessions, -c.headroom, int(c.number)))
    return WindowAdvice(target.number, start, len(phases))
