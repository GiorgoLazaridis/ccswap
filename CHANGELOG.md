# Changelog

All notable changes are documented here using Keep a Changelog 1.1.0.

## [Unreleased]

### Added

- Isolated Codex account sessions with guarded credential writers, reconciliation,
  process supervision and additive session status fields.
- Explicitly redeem a selected banked reset with expiry confirmation and no POST retries.

### Fixed

- Avoid account-store lock waits in observations when sessions have never been used.
- Keep backup quota reads available while the default login is temporarily unreadable,
  without allowing an unchecked token refresh or reconciliation.
- Skip isolated-session compatibility checks for the plain default-login shortcut;
  report the macOS isolated-run gate before binary discovery.
- Accept stable Codex versions from 0.164.0 with the required no-daemon capability;
  resolve older plain-start Windows npm installations independently of that minimum.
- Remove OPENAI_BASE_URL from isolated children and retain switch strategy parsing.
- Report an accepted reset as unconfirmed if its confirmation response is truncated.
- Refuse reset redemption for uncertain/conflicted sessions or changed credential identity;
  validate the default login before any refresh reconciliation.
