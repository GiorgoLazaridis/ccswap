# Codex CLI accounts

`ccswap` can also save and switch Codex CLI logins. Log into each account with `codex login`, then save it before logging into the next one:

```bash
codex login
ccswap codex add

# Log in to the next Codex account, then save it too.
codex login
ccswap codex add

ccswap codex list                 # accounts tagged by plan, e.g. [Codex Team]
ccswap codex usage                # Quota, credit allowance, and banked resets
ccswap codex switch 1
ccswap codex switch                 # rotate to the next saved account
ccswap codex switch --strategy best
ccswap codex switch --strategy next-available
ccswap codex switch --strategy consume-first
ccswap codex auto --once            # switch when active quota reaches the threshold
ccswap codex remove 2
```

Usage-aware strategies consider switchable, enabled ChatGPT accounts only; API-key accounts have no subscription quota and are skipped.

Codex switching preserves the rest of `CODEX_HOME` (configuration, skills, sessions, and history) and replaces only `auth.json`. Restart Codex after switching so its running process loads the selected login. For ChatGPT-backed file logins, the dashboard reads the same read-only Codex rate-limit endpoints used by Codex and shows the weekly window with a live reset countdown, the workspace/purchased credit balance, the per-user credit allowance when the service reports one, plus the number of banked resets and the earliest available reset's expiry. Credits, per-user allowances, and banked resets are shown separately because they are different resources. Window labels are derived from the duration reported by Codex, so older accounts that still return a 5-hour window remain correctly labelled. Each account is labelled by its ChatGPT plan (`Codex Team`, `Codex Pro`, `Codex Plus`, …) — read from the login token, the closest local equivalent to Claude Code's org name, since Codex stores no workspace name on disk. API-key accounts have no ChatGPT subscription quota, so they remain status-only. `ccswap codex auto` honors `autoswitch.strategy` (`best` or `consume-first`) and the per-window thresholds `autoswitch.threshold5h` and `autoswitch.threshold7d` (Codex Weekly), and uses the same threshold, cooldown, `--once`, `--dry-run`, and JSON event controls as Claude auto-switching; it prepares the account for the next Codex launch and reports when a restart is needed.

Codex must use its documented file credential store. If your `~/.codex/config.toml` says `cli_auth_credentials_store = "keyring"`, change it to `"file"`, run `codex login`, then add the account. This deliberate restriction avoids writing a guessed OS-keyring entry.
