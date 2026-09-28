# Repository instructions for agents

This repository is Giorgo's fork of `errhythm/ccswap`. Before describing a
feature as fork-specific, compare it with the shared upstream base and inspect
the implementing commit. Keep the upstream package and this Git fork distinct.

For every user-visible fork change, update **both** `CHANGELOG.md` (English)
and `CHANGELOG.de.md` (German) in the same commit as the change. Add a dated
entry under the applicable fork package version, or an `Unreleased` section
before the next version is assigned. State the behavior, practical limits, and
the implementing commit when available. Keep the two pages factually aligned.
CI enforces updates to both pages for every change to `src/`, `README.md`, or
`pyproject.toml`; it cannot judge whether the prose is accurate, so review it
against the implementation and history.
Do not attribute workstation hooks, scheduled tasks, or Codex skills to the
`ccswap` package unless that integration actually lives in this repository.

Keep the fork notice and links in `README.md` visible and working. When the
version changes, update both changelogs, the README's fork description if
needed, and the package's changelog URL. Check links and Git history before
publishing.
