"""`ccswap statusline` renders cached pool state plus Claude Code's session JSON."""

from __future__ import annotations

import io
import json

from claude_swap import cli, statusline
from claude_swap.models import AccountSnapshot, AccountsSnapshot
from claude_swap.usage_store import UsageEntry

NOW = 1_800_000_000.0


def account(number: str, *, five=None, weekly=None, weekly_reset=None,
            scoped=(), active=False, disabled=False, kind="oauth",
            alias="", switchable=True, sentinel=None) -> AccountSnapshot:
    usage: dict = {}
    if five is not None:
        usage["five_hour"] = {"pct": five, "resets_at": None}
    if weekly is not None:
        usage["seven_day"] = {"pct": weekly, "resets_at": weekly_reset}
    if scoped:
        usage["scoped"] = [{"name": n, "pct": p, "resets_at": None} for n, p in scoped]
    return AccountSnapshot(
        number=number, email=f"user{number}@example.test", org_name="",
        org_uuid=f"org-{number}", is_active=active, kind=kind, switchable=switchable,
        usage=UsageEntry(last_good=usage or None, age_s=30, sentinel=sentinel), alias=alias,
        disabled=disabled,
    )


def snap(*accounts: AccountSnapshot) -> AccountsSnapshot:
    active = next((a.number for a in accounts if a.is_active), None)
    return AccountsSnapshot(active_number=active, accounts=accounts, taken_at=NOW)


def test_active_account_and_exhausted_reserve_with_reset():
    line = statusline.render(
        snap(account("1", five=0, weekly=100, weekly_reset="2027-01-16T08:00:00+00:00"),
             account("2", five=9, weekly=3, active=True, alias="work")),
        now=NOW, color=False,
    )
    active, reserve = line.split(" │ ")
    assert active == "#2 work 5h 9% 7d 3%"
    assert reserve.startswith("#1 7d 100% ↻")


def test_session_rate_limits_override_cache_and_keep_other_windows():
    session = {
        "rate_limits": {"five_hour": {"used_percentage": 41.6}},
        "context_window": {"used_percentage": 12},
        "cost": {"total_cost_usd": 3.456},
    }
    line = statusline.render(
        snap(account("1", five=10, weekly=20, active=True)), session,
        now=NOW, color=False,
    )
    assert line == "#1 user1 5h 42% 7d 20% │ ctx 12% $3.46"


def test_configured_model_limit_is_shown_and_binds_reserve():
    line = statusline.render(
        snap(account("1", five=5, weekly=5, scoped=[("Fable", 95)]),
             account("2", five=1, weekly=1, scoped=[("Fable", 10)], active=True)),
        models=("Fable",), now=NOW, color=False,
    )
    assert line == "#2 user2 5h 1% 7d 1% Fable 10% │ #1 Fable 95%"


def test_disabled_api_key_and_unknown_accounts():
    line = statusline.render(
        snap(account("1", five=1, weekly=1, active=True),
             account("2", five=1, weekly=1, disabled=True),
             account("3", kind="api_key"),
             account("4")),
        now=NOW, color=False,
    )
    assert line == "#1 user1 5h 1% 7d 1% │ #4 ?"


def test_color_marks_thresholds():
    line = statusline.render(snap(account("1", five=95, weekly=10, active=True)), now=NOW)
    assert "\x1b[31m5h 95%" in line
    assert "\x1b[32m7d 10%" in line


def test_no_active_account():
    assert statusline.render(snap(account("1", five=1, weekly=1)), now=NOW,
                             color=False).startswith("ccswap: no active account")


def test_read_session_ignores_tty_and_bad_json():
    class Tty(io.StringIO):
        def isatty(self):
            return True

    assert statusline.read_session(Tty('{"a": 1}')) is None
    assert statusline.read_session(io.StringIO("not json")) is None
    assert statusline.read_session(io.StringIO("[1]")) is None
    assert statusline.read_session(io.StringIO(json.dumps({"a": 1}))) == {"a": 1}


def test_cli_dispatch_never_fails_the_prompt(monkeypatch, capsys):
    def broken(**_kwargs):
        raise RuntimeError("store unreadable")

    monkeypatch.setattr("claude_swap.switcher.ClaudeAccountSwitcher", broken)
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    monkeypatch.setattr("sys.argv", ["ccswap", "statusline", "--no-color"])
    cli.main()
    assert capsys.readouterr().out.strip() == "ccswap: RuntimeError"


def test_statusline_never_probes_the_terminal():
    from claude_swap.appearance import cli_should_probe

    assert cli_should_probe(["statusline"], colors_enabled=True) is False


def test_unusable_reserve_is_not_advertised_with_old_percentages():
    line = statusline.render(
        snap(account("1", five=1, weekly=1, active=True),
             account("2", five=5, weekly=5, sentinel="token expired"),
             account("3", five=5, weekly=5, switchable=False)),
        now=NOW, color=False,
    )
    assert line == "#1 user1 5h 1% 7d 1% │ #2 token expired #3 no credentials"


def test_failing_snapshot_writes_no_log_and_empty_no_color_disables_color(
    monkeypatch, capsys
):
    import logging

    written = []

    class Recorder(logging.Handler):
        def emit(self, record):
            written.append(record)

    def broken(**_kwargs):
        logging.getLogger("claude-swap").error("roster unreadable")
        raise RuntimeError("store unreadable")

    logger = logging.getLogger("claude-swap")
    handler = Recorder()
    logger.addHandler(handler)
    monkeypatch.setattr("claude_swap.switcher.ClaudeAccountSwitcher", broken)
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    monkeypatch.setenv("NO_COLOR", "")
    try:
        statusline.main([])
    finally:
        logger.removeHandler(handler)
        logging.disable(logging.NOTSET)
    assert written == []
    assert capsys.readouterr().out.strip() == "ccswap: RuntimeError"
