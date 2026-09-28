# Fork changelog (English)

[Deutsch](CHANGELOG.de.md) · [README](README.md) · [Upstream](https://github.com/errhythm/ccswap)

This log covers changes in Giorgo's fork **relative to the shared upstream
commit [`14df53c` (v0.35.1)](https://github.com/errhythm/ccswap/commit/14df53cf3eba7c86a10d03698b958f7ba809b1c4)**.
It does not repeat features already present upstream. Dates are commit dates.
The [`v0.35.1-gl.1` Git tag](https://github.com/GiorgoLazaridis/ccswap/tree/v0.35.1-gl.1)
still declares package version `0.35.1`; the `+gl.*` package versions begin
with `0.36.0+gl.1`.

## Unreleased

- **Doctor finds competing CLI installs.** `ccswap doctor` and `health` warn
  when more than one `PATH` directory provides `claude` or `codex` (for
  example a native build next to an npm shim on Windows) and list them in
  lookup order, since the first silently wins and updates may land in the
  other copy.
- **Claude Code status line.** `ccswap statusline` prints the active account
  with its 5h/7d and configured per-model usage, the other rotation accounts'
  binding windows with reset countdowns, and context/cost from Claude Code's
  session JSON. It only reads the existing cache: no usage request, token
  refresh, or file write. Errors print a short `ccswap: <error>` line.
- **Smart launch honors per-model weekly limits.** `ccswap run --smart` and
  `ccswap plan` now apply the `autoswitch.model` setting (for example
  `Fable`) the same way auto-switch does: an account whose configured model
  is at its weekly limit is skipped, and that window narrows the ranked
  headroom. Models an account does not report are not invented;
  `autoswitch.windows` remains limited to `ccswap auto`.

## 0.36.0+gl.2 — 2026-09-28

- **Fork documentation.** The README now identifies this Git fork, links this
  bilingual history, and distinguishes the upstream PyPI package. Package
  metadata points to the fork's repository, issues, and changelog. Repository
  instructions and CI require both languages for future source changes; the
  CI workflow can also be started manually for verification, including when
  there is no prior commit to compare. The README also links the full Git
  comparison with the upstream base.
- **One continuous auto loop per provider.** A second `ccswap auto` or
  `ccswap codex auto` exits with an error instead of polling and switching in
  parallel. The one-shot `--once` commands remain available. [Commit](https://github.com/GiorgoLazaridis/ccswap/commit/23ea59dd39e3151b62c814f6e132eb8d5de699d4)
- **Codex quota events.** JSON poll events now include each account's 5-hour
  and 7-day usage percentages, so a supervisor can detect either window's
  threshold from the existing poll without another quota request. [Commit](https://github.com/GiorgoLazaridis/ccswap/commit/23ea59dd39e3151b62c814f6e132eb8d5de699d4)
- **Safer Codex decisions.** A failed current usage fetch no longer lets a
  cached last-good value masquerade as a fresh switching decision. [Commit](https://github.com/GiorgoLazaridis/ccswap/commit/23ea59dd39e3151b62c814f6e132eb8d5de699d4)

## 0.36.0+gl.1 — 2026-09-26

- **Smart native Claude launch.** `ccswap run --smart` selects an eligible
  isolated Claude profile using quota headroom, measurement trust, active
  sessions, and hard directory mappings. `--explain` and `--dry-run` show the
  choice; `ccswap plan [--json] [--refresh]` provides a read-only preview and
  a passive 5-hour window suggestion when reset data support it. This does
  not provide a Codex isolated launcher or change `ccswap auto` policy.
  [Feature](https://github.com/GiorgoLazaridis/ccswap/commit/4b01430fe662364f0f20bf620e9cb484067ce4c1) ·
  [trust fix](https://github.com/GiorgoLazaridis/ccswap/commit/715a8f5ca107dc7b5370c5806f42ef14bc2f4154)
- **Read-only diagnostics.** `ccswap doctor` and `ccswap health` inspect
  local CLI, roster, settings, quota cache, and endpoint overrides, with JSON
  output. They do not prove token validity or Windows ACL health.
  [Commit](https://github.com/GiorgoLazaridis/ccswap/commit/55dd58dc58186dc0646d1a7b3dc3ad6591b3e443)
- **Session safety.** Unreadable process records prevent profile changes;
  smart launch rejects nested `CLAUDE_CONFIG_DIR` and rechecks the default
  login before launch. A short race with a concurrent default switch remains
  documented. [Process fix](https://github.com/GiorgoLazaridis/ccswap/commit/2a01d53) ·
  [preview/profile fix](https://github.com/GiorgoLazaridis/ccswap/commit/4d19c6f) ·
  [login recheck](https://github.com/GiorgoLazaridis/ccswap/commit/3c66a56)
- **Identifiable fork installation.** The package gained the `+gl.1` suffix;
  fork builds suppress misleading upstream PyPI update prompts and self
  upgrade. Install or update this fork from Git with `uv tool install`.
  [Commit](https://github.com/GiorgoLazaridis/ccswap/commit/9864849)
- **Architecture record.** The [native session allocation](docs/native-session-allocation.md)
  design and [German v2 roadmap](docs/ccswap-v2-roadmap.de.md) describe
  implemented scope and open risks. [Commit](https://github.com/GiorgoLazaridis/ccswap/commit/c237155)

## v0.35.1-gl.1 — 2026-09-26 (Git tag)

- **Atomic account roster publication on Windows.** `sequence.json` now uses
  atomic replacement so concurrent readers do not see a partial file.
  [Commit](https://github.com/GiorgoLazaridis/ccswap/commit/68ffc97)
- **Windows process decoding.** `tasklist` uses the OEM code page and tolerates
  undecodable bytes; `claude auth status --json` is read as UTF-8. This fixes
  Codex auto-switch ticks failing on localized Windows output.
  [Commit](https://github.com/GiorgoLazaridis/ccswap/commit/aa023fc)

Fork-only changes after these entries are documented here and in
[CHANGELOG.de.md](CHANGELOG.de.md). Upstream releases have their own
[release history](https://github.com/errhythm/ccswap/releases).
