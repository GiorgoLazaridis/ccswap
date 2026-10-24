# JSON output for scripting

Add `--json` to `list`, `status`, or `switch` to emit a single machine-readable JSON object on stdout (human-readable notices go to stderr). Useful for scripting auto-swap and quota tracking.

```bash
ccswap list --json                   # BOTH providers, merged (default)
ccswap list --provider claude --json # Claude Code accounts only (bare payload)
ccswap list --provider codex --json  # Codex accounts only (bare payload)
ccswap status --json                 # current active account
ccswap switch --strategy best --json # switch, then report the result
ccswap switch 2 --json
```

`ccswap list` now covers both providers by default. In `--json` mode that means the output is keyed by provider (`claude`/`codex`); pass `--provider claude` (or `codex`) to get a single provider's **bare** payload — the same shape older scripts already parse.

## Example output & schema notes

Merged (default) — `ccswap list --json`:

```json
{
  "claude": {
    "schemaVersion": 1,
    "activeAccountNumber": 2,
    "accounts": [
      { "number": 2, "email": "you@example.com", "active": true, "usageStatus": "ok",
        "usage": { "fiveHour": { "pct": 25.0, "resetsAt": "2026-06-22T23:29:59Z" },
                   "sevenDay": { "pct": 16.0, "resetsAt": "2026-06-26T17:59:59Z" } } }
    ]
  },
  "codex": {
    "provider": "codex",
    "activeAccountNumber": 1,
    "accounts": [
      { "number": 1, "email": "you@example.com", "authMode": "chatgpt",
        "planType": "team", "label": "Codex Team", "active": true }
    ]
  }
}
```

Single provider — `ccswap list --provider claude --json` — returns just the inner Claude object above (`schemaVersion`, `activeAccountNumber`, `accounts`), unchanged from before. **Migration:** scripts that parsed the old top-level `accounts` from `ccswap list --json` should switch to `ccswap list --provider claude --json`.

Every Claude payload carries a `schemaVersion` (currently `1`); on a handled error stdout is `{"schemaVersion":1,"error":{...}}` with a non-zero exit code. `--switch`/`--switch-to` report `{"switched": true|false, "from": …, "to": …, "reason": …}`.

A row carries an additive `loginExpiresAt` (ISO-8601 UTC) when the stored login records when its refresh token expires, which is the moment the slot will need a fresh `/login` and `ccswap add --slot N`; a script can warn a few days ahead instead of discovering `relogin_required`. Absent when Claude Code recorded no such date for that login.

Usage is served from a per-account cache: when the usage API is briefly unreachable, the last-known numbers are shown instead of nothing (the human view marks them with their age, e.g. `· 2m ago`). Rows with decision-trusted usage carry additive `usageFetchedAt`/`usageAgeSeconds` fields telling you how old the measurement is. Whenever `usage` is null but a last-known measurement exists — data too old to drive a decision (`usageStatus` stays `unavailable`), or a row in a non-`ok` state such as `token_expired` — additive `lastGoodUsage`/`lastGoodFetchedAt`/`lastGoodAgeSeconds` fields preserve the human display without making the account actionable. When `usage` is null and nothing else explains it (`usageStatus` is `unavailable`), an additive `usageError` names the last fetch failure by kind (e.g. `http-429`, `timeout`) and, while the cache is backing off from it, `usageRetryAt` gives the time of the next attempt. These fields apply to list rows and the managed active row from `status --json`. An account held out of rotation with `ccswap disable` carries an additive `"disabled": true` on its row (absent otherwise).

An account row also carries an additive `alias` field once one is set with `ccswap alias` (e.g. `"alias": "dev"`); accounts without one simply omit the key.

Weekly windows (`sevenDay` and per-model `scoped` entries — never `fiveHour`) additively carry pace fields once the week is ~a day old: `expectedPct` (where usage would sit if spread evenly across the week) and `aheadOfPace` (`true` when meaningfully above that — the same signal the human views show as an `(ahead)`/`(ahead of pace)` marker). `projectedExhaustionAt`/`willLastToReset` extrapolate the current rate into an ETA to 100% and a yes/no "will it last to the reset"; they stay `--json`-only since a linear projection is too rough to present as fact in the UI.

`ccswap auto --json` emits an event *stream* instead — one JSON object per line (`{"schemaVersion":1,"event":"switch","ts":…, …}` with kinds like `poll`, `switch`, `no-switch`, `account-quarantined`, `all-exhausted`, `error`). The contract is additive: new kinds and fields may appear, so scripts should ignore unknown ones.
