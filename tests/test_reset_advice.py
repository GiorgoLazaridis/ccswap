"""Banked Codex reset advice: spacing, urgency, and whether a reset pays off now."""

from __future__ import annotations

from datetime import datetime, timezone

from claude_swap import reset_advice
from claude_swap.reset_advice import DAY_S, WEEK_S, advise, deadlines

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc).timestamp()


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def usage(*expiry_days: float, weekly: float = 20, weekly_reset_days: float = 5) -> dict:
    expiries = [iso(NOW + d * DAY_S) for d in expiry_days]
    return {
        "weekly": {"pct": weekly, "resets_at": iso(NOW + weekly_reset_days * DAY_S)},
        "reset_credits": {"available": len(expiries), "expires_at": min(expiries),
                          "expiries": sorted(expiries)},
    }


def test_deadlines_leave_a_week_before_each_later_reset():
    e = [NOW + 6 * DAY_S, NOW + 7 * DAY_S, NOW + 24 * DAY_S]
    assert deadlines(e) == [NOW + 7 * DAY_S - WEEK_S, NOW + 7 * DAY_S, NOW + 24 * DAY_S]


def test_two_resets_a_day_apart_force_redeeming_the_first_now():
    # The situation that motivated this: expiries Oct 4 and Oct 5.
    advice = advise(usage(6, 7), NOW)
    assert advice.urgency == "red"
    assert advice.worth_now
    assert advice.reason == "later resets need the week after it"


def test_single_far_reset_is_kept():
    advice = advise(usage(25), NOW)
    assert (advice.urgency, advice.worth_now) == ("green", False)
    assert advice.reason == "keep it until the weekly limit is near"


def test_reset_expiring_within_a_week_is_orange():
    advice = advise(usage(5), NOW)
    assert (advice.urgency, advice.worth_now) == ("orange", False)


def test_reset_expiring_within_two_days_is_red_and_used():
    advice = advise(usage(1.5), NOW)
    assert (advice.urgency, advice.worth_now) == ("red", True)
    assert advice.reason == "expires unused otherwise"


def test_blocked_week_makes_a_reset_worth_it_unless_the_week_resets_soon():
    assert advise(usage(25, weekly=95, weekly_reset_days=4), NOW).worth_now
    soon = advise(usage(25, weekly=95, weekly_reset_days=0.5), NOW)
    assert not soon.worth_now
    assert soon.reason == "weekly resets within a day anyway"


def test_count_only_and_invalid_payloads():
    assert advise({"reset_credits": {"available": 0}}, NOW) is None
    assert advise({"reset_credits": {"available": True}}, NOW) is None
    assert advise(None, NOW) is None
    only_count = advise({"reset_credits": {"available": 2}}, NOW)
    assert (only_count.count, only_count.deadline, only_count.urgency) == (2, None, "green")


def test_earliest_expiry_fallback_without_detail_list():
    data = {"reset_credits": {"available": 1, "expires_at": iso(NOW + 3 * DAY_S)}}
    assert advise(data, NOW).urgency == "orange"


def test_list_line_and_statusline_badge():
    from claude_swap import codex, statusline

    snapshot = {"accounts": {"1": {"usage": usage(6, 7)}}}
    line = codex._cached_reset_line(snapshot, "1", now=NOW)
    assert line.startswith("Resets 2 · expire ")
    assert line.endswith("redeem one now (later resets need the week after it)")
    assert statusline._reset_badge(usage(6, 7), NOW, color=False) == "R2!"
    assert statusline._reset_badge(usage(25), NOW, color=False) == "R1"
    assert statusline._reset_badge(usage(25), NOW, color=True) == f"{statusline._GREEN}R1{statusline._RESET}"
    assert reset_advice.SOON_S == 7 * DAY_S
