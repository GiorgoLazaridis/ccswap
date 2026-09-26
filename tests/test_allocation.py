"""Session allocation decisions use only trusted local read models."""

from __future__ import annotations

from dataclasses import replace
import json
from unittest.mock import MagicMock

import pytest

from claude_swap import allocation
from claude_swap import cli
from claude_swap.exceptions import ConfigError
from claude_swap.models import AccountSnapshot, AccountsSnapshot
from claude_swap.switcher import ClaudeAccountSwitcher
from claude_swap.usage_store import UsageEntry


def account(number: str, five: float | None, weekly: float | None,
            *, age: float = 20, active: bool = False,
            disabled: bool = False) -> AccountSnapshot:
    usage = {}
    if five is not None:
        usage["five_hour"] = {"pct": five}
    if weekly is not None:
        usage["seven_day"] = {"pct": weekly}
    return AccountSnapshot(
        number=number, email=f"account{number}@example.test", org_name="",
        org_uuid=f"org-{number}", is_active=active, kind="oauth",
        switchable=True, usage=UsageEntry(last_good=usage, age_s=age),
        disabled=disabled,
    )


def plan(tmp_path, monkeypatch, accounts, *, mapped=None, scans=None, now=100):
    scans = scans or {}
    monkeypatch.setattr(
        allocation, "scan_live_sessions",
        lambda path: ([object()] * scans.get(path.name.split("-")[0], (0, 0))[0],
                      scans.get(path.name.split("-")[0], (0, 0))[1]),
    )
    return allocation.allocate(
        AccountsSnapshot(
            active_number=next((a.number for a in accounts if a.is_active), None),
            accounts=tuple(accounts), taken_at=now,
        ),
        backup_dir=tmp_path, mapped_account=mapped,
    )


def test_selects_headroom_and_uses_sessions_within_similarity_band(
    tmp_path, monkeypatch
):
    result = plan(
        tmp_path, monkeypatch,
        [account("1", 10, 10, active=True), account("2", 20, 30),
         account("3", 25, 35)],
        scans={"2": (2, 0), "3": (0, 0)},
    )
    assert result.selected == "3"
    assert "0 active sessions" in result.candidates[2].reasons


def test_hard_mapping_is_honored_and_never_silently_falls_back(
    tmp_path, monkeypatch
):
    accounts = [account("1", 20, 20), account("2", 90, 90)]
    assert plan(tmp_path, monkeypatch, accounts, mapped="2").selected == "2"
    blocked = plan(tmp_path, monkeypatch,
                   [accounts[0], replace(accounts[1], usage=UsageEntry())],
                   mapped="2")
    assert blocked.selected is None
    assert "Mapped account" in blocked.error


def test_unknown_stale_and_unreadable_processes_are_not_guessed(
    tmp_path, monkeypatch
):
    result = plan(
        tmp_path, monkeypatch,
        [account("1", 10, 10, active=True), account("2", None, 10),
         account("3", 10, 10, age=301), account("4", 10, 10)],
        scans={"4": (0, 1)},
    )
    assert result.selected is None
    assert all(c.skipped for c in result.candidates)


def test_store_trusted_stale_is_eligible_but_fresh_peer_wins_close_rank(
    tmp_path, monkeypatch
):
    stale = account("2", 20, 20, age=600)
    stale = replace(stale, usage=replace(stale.usage, trust_extended=True))
    fresh = account("3", 25, 25)
    result = plan(tmp_path, monkeypatch,
                  [account("1", 10, 10, active=True), stale, fresh])
    assert result.selected == "3"
    assert result.candidates[1].skipped is None
    assert result.candidates[1].stale is True
    assert "actual headroom may be lower" in result.candidates[1].reasons[-1]


def test_disabled_is_skipped_unless_explicit_project_mapping(tmp_path, monkeypatch):
    accounts = [account("1", 10, 10, active=True),
                account("2", 10, 10, disabled=True)]
    assert plan(tmp_path, monkeypatch, accounts).selected is None
    assert plan(tmp_path, monkeypatch, accounts, mapped="2").selected == "2"


def test_passive_largest_gap_moves_when_a_natural_window_starts(
    tmp_path, monkeypatch
):
    now = 10 * 3600

    def observed(number, start_hour, start_minute, *, active=False):
        item = account(number, 20, 20, active=active)
        reset = (start_hour + 5) * 3600 + start_minute * 60
        usage = {
            "five_hour": {"pct": 20, "resets_at":
                          f"1970-01-01T{reset // 3600:02d}:{(reset // 60) % 60:02d}:00Z"},
            "seven_day": {"pct": 20},
        }
        return replace(item, usage=UsageEntry(last_good=usage, age_s=20))

    a = observed("1", 8, 0, active=True)
    b = observed("2", 8, 10)
    c = account("3", 0, 20)
    first = plan(tmp_path, monkeypatch, [a, b, c], now=now)
    assert first.window_advice is not None
    assert first.window_advice.account == "3"
    assert first.window_advice.ideal_start_ts == 10 * 3600 + 35 * 60

    c_started = observed("3", 10, 30)
    second = plan(tmp_path, monkeypatch, [a, b, c_started], now=now + 31 * 60)
    assert second.window_advice is None


def test_smart_run_pins_native_session_and_dry_run_never_launches(
    monkeypatch, capsys
):
    selected = allocation.Candidate(
        number="2", email="two@example.test", alias="", five_hour_used=20,
        weekly_used=30, age_s=15, sessions=0,
        reasons=("5h headroom 80%",),
    )
    decision = allocation.AllocationPlan("2", (selected,), None, None)
    switcher = MagicMock()
    manager = MagicMock()
    refreshed = []
    constructed = []
    monkeypatch.setattr(
        cli, "ClaudeAccountSwitcher",
        lambda **kw: (constructed.append(kw) or switcher),
    )
    monkeypatch.setattr(cli, "_guard_root", lambda _switcher: None)
    monkeypatch.setattr(cli, "_native_session_plan",
                        lambda _switcher, *, refresh: (
                            refreshed.append(refresh) or decision
                        ))
    monkeypatch.setattr("claude_swap.session.SessionManager", lambda _s: manager)

    cli._run_command(["--smart", "--dry-run"])
    manager.run.assert_not_called()
    assert refreshed == [False]
    assert constructed[-1]["read_only"] is True

    cli._run_command(["--smart", "--", "--resume", "conversation-id"])
    manager.run.assert_called_once_with(
        "2", ["--resume", "conversation-id"], share=True,
        share_history=False, require_session=True,
    )
    assert refreshed == [False, True]
    assert constructed[-1]["read_only"] is False
    assert "Claude account selected" in capsys.readouterr().out


def test_read_only_preview_does_not_heal_usage_store_strikes():
    switcher = MagicMock()
    switcher._read_only = True
    switcher._poll_policy_inputs.return_value = (90.0, (), ("5h", "7d"))
    switcher._static_usage_sentinel.return_value = None
    switcher._entry_token_dead.return_value = False
    switcher._usage_store.entries.return_value = {
        "2": UsageEntry(auth_dead_strikes=100, last_good={}, age_s=10)
    }
    switcher._usage_store.reserve.return_value = {}
    info = (2, "two@example.test", "", "org-2", False, "credentials", "")

    ClaudeAccountSwitcher._collect_usage_entries(switcher, [info], fetch=set())

    switcher._usage_store.clear_dead_token.assert_not_called()
    switcher._usage_store.reserve.assert_called_once()


def test_smart_run_refuses_nested_claude_profile(monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "C:\\isolated-claude")
    with pytest.raises(SystemExit) as exc:
        cli._run_command(["--smart", "--dry-run"])
    assert exc.value.code == 2


def test_plan_json_preserves_error_envelope(monkeypatch, capsys):
    def corrupt_store(**_kwargs):
        raise ConfigError("corrupt roster")

    monkeypatch.setattr(cli, "ClaudeAccountSwitcher", corrupt_store)
    with pytest.raises(SystemExit) as exc:
        cli._plan_command(["--json"])
    assert exc.value.code == 1
    assert json.loads(capsys.readouterr().out)["error"]["type"] == "ConfigError"
