"""Shared option help for hover and keyboard use."""

from typing import Callable
from claude_swap.i18n import mark

OPTION_HELP = {
    "switch": mark(
        "Choose a saved account to use. Codex must be restarted after switching to load the selected login."
    ),
    "watch": mark(
        "Monitor account quotas without switching. Press s in Watch to enable account selection."
    ),
    "auto": mark(
        "Open the auto-switch view for a provider. It starts as a dry-run preview; press l and confirm to switch live. Closing the view stops its engine."
    ),
    "add-menu": mark(
        "Save your current login, or add a Claude setup token or API key, as a managed account."
    ),
    "disable-menu": mark(
        "Exclude an account from automatic selection without deleting its saved login. You can enable it again here."
    ),
    "remove-menu": mark(
        "Choose an account whose saved credentials and configuration backup will be deleted. You will be asked to confirm."
    ),
    "settings-menu": mark(
        "Change appearance, account masking, switching policy and Claude Code integrations. Settings are saved for later runs."
    ),
    "quit": mark(
        "Close this app. Any auto-switch engine started by this app stops; separately running auto processes are not stopped."
    ),
    "back": mark("Return to the previous menu without changing a setting."),
    "setting:theme": mark(
        "Cycle dark, light and auto. Auto follows the terminal background."
    ),
    "setting:view": mark(
        "Cycle both providers, Claude only and Codex only. This filters the dashboard, Switch and Watch lists; administrative menus still show all accounts."
    ),
    "setting:mask": mark(
        "Hide account emails and organization names in supported displays. This does not remove credentials or change the active login."
    ),
    "setting:threshold": mark(
        "Cycle the default utilization threshold for automatic switching. More specific gates override it; weekly gates may inherit weeklyThreshold first. Disabled windows do not trigger switching."
    ),
    "setting:threshold5h": mark(
        "Cycle the 5-hour gate, then inherit. Automatic switching can trigger when any configured gate reaches its threshold."
    ),
    "setting:threshold7d": mark(
        "Cycle the weekly gate, then inherit. Applies to Claude's 7-day window and Codex's weekly window."
    ),
    "setting:fable": mark(
        "Cycle the Claude-only Fable weekly-model gate, then inherit. Other model thresholds are preserved. Exact values and additional models are available through ccswap config set."
    ),
    "setting:strategy": mark(
        "Cycle best and consume-first. Best favors quota headroom; consume-first proactively uses available quota whose weekly window resets sooner, even below the threshold. This does not redeem banked resets."
    ),
    "claude:claude.statusline": mark(
        "Install or remove ccswap's Claude Code statusline. Turning it off restores the statusline configuration backed up during installation."
    ),
    "claude:claude.mod": mark(
        "Enable or disable the ccswap Claude Code plugin. This may invoke Claude's plugin manager; it does not change your account."
    ),
    "add-token": mark(
        "Add a Claude setup token or API key. API-key requests are billed by usage; a setup token uses its account subscription."
    ),
    "add-login": mark(
        "Save the provider's current login as a managed account. An already managed login is refreshed in place."
    ),
    "remove": mark(
        "Remove this account's saved credentials and configuration backup after confirmation. Removal cannot be undone from this menu."
    ),
    "disable": mark(
        "Toggle whether this account may be selected automatically. Its saved login remains stored."
    ),
    "setting:language": mark(
        "Choose the TUI language. The choice is saved; restart the dashboard to apply it. Commands, keyboard keys and account settings keep their original values."
    ),
}


def option_help(action_id: str, tr: Callable[[str], str] = str) -> str | None:
    message = OPTION_HELP.get(action_id) or OPTION_HELP.get(action_id.split(":", 1)[0])
    return tr(message) if message else None
