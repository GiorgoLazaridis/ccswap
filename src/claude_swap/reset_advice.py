"""When to redeem banked Codex rate-limit resets.

A banked reset refreshes the 5-hour and weekly Codex windows and starts a new
weekly window at redemption; each reset expires on its own date. Two resets
redeemed a day apart therefore waste most of the first one: the second wipes
a week that was barely used. The advice below plans backwards from the
expiries so that every reset can cover a full week before the next one is
due, then rates how urgent the earliest one is and whether redeeming now
buys real headroom.

Only Codex reports resets through its usage API. Claude's limit resets are
visible and redeemable in claude.ai only, so nothing here applies to them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

WEEK_S = 7 * 24 * 3600
DAY_S = 24 * 3600
URGENT_S = 2 * DAY_S        # red: redeem now or lose it
SOON_S = 7 * DAY_S          # orange: plan it within the week
WORTH_PCT = 90.0            # weekly use at which a reset buys real headroom
WORTH_MIN_WAIT_S = DAY_S    # ...unless the natural weekly reset is this close


@dataclass(frozen=True)
class ResetAdvice:
    count: int
    expiries: tuple[float, ...]
    deadline: float | None      # latest sensible redemption of the next reset
    urgency: str                # "green" | "orange" | "red" | "unknown"
    worth_now: bool
    reason: str


def _ts(value: object) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def deadlines(expiries: list[float]) -> list[float]:
    """Latest redemption per reset (ascending) that still leaves a week each.

    The last reset may wait until it expires; every earlier one must be used
    a week before the next deadline, and never after its own expiry.
    """
    ordered = sorted(expiries)
    result = [0.0] * len(ordered)
    following: float | None = None
    for index in range(len(ordered) - 1, -1, -1):
        latest = ordered[index]
        if following is not None:
            latest = min(latest, following - WEEK_S)
        result[index] = latest
        following = latest
    return result


def advise(usage: object, now: float) -> ResetAdvice | None:
    """Advice for one Codex account's usage dict, or None without resets."""
    if not isinstance(usage, dict):
        return None
    credits = usage.get("reset_credits")
    if not isinstance(credits, dict):
        return None
    count = credits.get("available")
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        return None
    raw = credits.get("expiries")
    if not isinstance(raw, list):
        raw = [credits.get("expires_at")]
    expiries = tuple(sorted(t for t in (_ts(v) for v in raw) if t is not None))

    plan = deadlines(list(expiries))
    deadline = plan[0] if plan else None
    left = None if deadline is None else deadline - now
    if left is None:
        urgency = "unknown"
    elif left > SOON_S:
        urgency = "green"
    elif left > URGENT_S:
        urgency = "orange"
    else:
        urgency = "red"
    # A reset without a known expiry may run out at any time: never call it safe.
    if len(expiries) < count and urgency in ("green", "orange"):
        urgency = "unknown"

    weekly = usage.get("weekly") or usage.get("seven_day")
    pct = weekly.get("pct") if isinstance(weekly, dict) else None
    weekly_reset = _ts(weekly.get("resets_at")) if isinstance(weekly, dict) else None
    blocked = isinstance(pct, (int, float)) and not isinstance(pct, bool) and pct >= WORTH_PCT
    wait = None if weekly_reset is None else weekly_reset - now

    if urgency == "red":
        spaced = bool(expiries) and deadline is not None and deadline < expiries[0]
        worth, reason = True, ("later resets need the week after it" if spaced
                               else "expires unused otherwise")
    elif blocked and (wait is None or wait > WORTH_MIN_WAIT_S):
        worth, reason = True, f"weekly {pct:.0f}% used"
    elif blocked:
        worth, reason = False, "weekly resets within a day anyway"
    elif urgency == "unknown":
        worth, reason = False, "expiry dates unknown; check the ChatGPT usage page"
    elif left is not None and left <= SOON_S:
        worth, reason = False, "use it this week, ideally once the weekly limit is near"
    else:
        worth, reason = False, "keep it until the weekly limit is near"
    return ResetAdvice(count, expiries, deadline, urgency, worth, reason)
