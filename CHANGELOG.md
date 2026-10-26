# Changelog

All notable changes are documented in this file using Keep a Changelog 1.1.0.

## [Unreleased]

### Fixed

- Refuse Claude session-profile mutations when the session-record directory cannot
  be enumerated, instead of treating an unsuccessful scan as proof of no live sessions.

### Added

- Honor shared Codex auto-switch strategies and per-window thresholds.
