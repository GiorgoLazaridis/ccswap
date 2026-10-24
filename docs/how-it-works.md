# How it works

## Restarts and session behavior

- **Do you need to restart after switching?** For **Claude Code**, usually not. On **Linux and Windows**, credentials are stored in a file and Claude Code re-reads them whenever that file changes, so the new account takes effect on your next message — no restart needed. On **macOS**, credentials live in the Keychain, which Claude Code caches for about 30 seconds; a running session picks up the switch once that cache expires. Restart Claude Code (or close and reopen the VS Code extension tab) only if you want the change to apply instantly.
- **Codex always needs a restart.** Unlike Claude Code, the Codex CLI reads `auth.json` once when it starts and keeps the login in memory — it never re-reads the file or caches it on a timer. A running Codex session therefore keeps using the old account no matter what you swap underneath it. `ccswap codex switch` and `ccswap codex auto` write the selected login and tailor the reminder to your machine: if a Codex process is running they tell you to quit and relaunch it (or start a new session / reload the IDE extension); if none is running they confirm the account is ready for the next launch. This is a limitation of the Codex CLI, not ccswap — there is no flag, signal, or config setting that makes a live Codex reload credentials, and ccswap deliberately does not kill your running Codex process for you.
- **Continuing sessions after switching:** You can keep using the same Claude Code session after switching — run `ccswap switch` in any terminal and carry on. If you'd prefer a clean start, close and reopen Claude Code (or the VS Code extension tab) and use `--resume` to pick your previous session. Either way, the first message on the new account may use extra usage as its conversation cache rebuilds.

## What happens on add and switch

- Backs up provider credentials when you add an account
- Swaps only the account-specific Claude login (or Codex's file-backed
  `auth.json`) when you switch accounts; live account-independent OAuth state
  (such as MCP server logins) is preserved instead of being overwritten by a
  slot's older snapshot
- Account credentials stored securely using platform-appropriate methods
- Switches (manual and automatic) hold Claude Code's own credential locks while writing, so a swap never interleaves with a token refresh
- Auto-switch freshens a target's token before activating it, and quarantines accounts whose refresh token has died (recover by re-adding it with `ccswap add --slot N`, or by replacing its stored credentials from a known-good export — a plain `ccswap import backup.cswap` replaces dead-token slots automatically)
- Usage numbers refresh every few minutes — faster for an account being used or close to switching, slower for idle ones — keeping ccswap comfortably inside Anthropic's rate limits however many dashboards you keep open on a machine. An age note like `· 6m ago` just means the next scheduled check hasn't come yet, not that something is stuck.
- Codex usage checks refresh inactive saved logins when possible; running Codex sessions must be restarted after a Codex account switch

## Where ccswap stores data

| Platform | Credentials | Config backups |
|----------|-------------|----------------|
| Windows | File-based (inside the backup directory, under `credentials/`) | `~/.claude-swap-backup/` |
| macOS | macOS Keychain | `~/.claude-swap-backup/` |
| Linux / WSL | File-based (inside the backup directory, under `credentials/`) | `${XDG_DATA_HOME:-~/.local/share}/claude-swap/` |

Session-mode profiles (`ccswap run`) live under the backup directory in `sessions/`. Tool preferences (`settings.json`) and auto-switch state (`autoswitch_state.json` — cooldown and quarantined accounts; delete it to reset) live in the backup directory root.

On Linux/WSL, set `XDG_DATA_HOME` to override the default location.
