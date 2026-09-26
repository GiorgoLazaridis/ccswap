# Native session allocation (first v2 slice)

`ccswap auto` and `ccswap run --smart` make decisions at different times.
`auto` retains its existing live default-login switch, threshold, hysteresis,
cooldown, and polling policy. Smart allocation selects an account once, then
starts an isolated, unmodified Claude Code process through `SessionManager`.
ccswap does not handle Claude prompts or responses.

## Decision boundary

`allocation.allocate` consumes one `AccountsSnapshot` and local Claude process
records. It excludes the active default login because a second credential copy
could diverge during token rotation. It also excludes API-key accounts, missing
credentials, disabled accounts without a hard project mapping, unknown or
decision-untrusted 5h/7d usage, exhausted windows, and unreadable process
records. A broken hard mapping fails explicitly. Within ten percentage points
of the best binding 5h/7d headroom, fewer live sessions win; fresher trusted
measurements break ties ahead of session count. The preview explains every
eligible or skipped candidate. `--dry-run` and ordinary `plan` read the cache
without refreshing tokens or modifying credentials. `plan --refresh` and a real
smart launch use the existing bounded usage collector.

The five-hour placement hint uses observed nonzero usage and reset times. It
suggests the midpoint of the largest cyclic gap for an unused account. It is
advisory: a real user session is the only way a new window starts, and the hint
never holds up an immediate launch. Reset timestamps alone do not prove a
window's start, so incomplete measurements produce no hint.

## Safety and current limits

The launch checks that the chosen account is outside the default login both
before and after profile setup. The final check narrows the concurrent
`ccswap auto` race, but a switch in the short interval before Claude records
its process remains possible. A durable launch lease shared with `auto` would
close that interval; it requires changes to the established credential-locking
path and should be designed and tested as a separate change.

Smart selection currently covers Claude Code profiles. Codex uses different
credential loading and has no matching isolated launcher. `doctor` reports
local configuration and endpoint overrides; it does not prove that a token is
valid, that quota endpoints are contractually approved, or that Windows ACLs
are correct. No provider request is sent by `doctor`.

The present cache has too little reliable history for a burn-rate forecast or
pool blackout claim. Reserve roles, soft affinity, smart resume, encrypted
exports, and TUI changes are deferred until their behavior and migration path
can be verified. Existing `auto` semantics and defaults remain intact.

## External constraints

Anthropic's [Consumer Terms](https://www.anthropic.com/legal/consumer-terms)
and OpenAI's [EU Terms](https://openai.com/policies/eu-terms-of-use/) warrant
careful review before adding automated usage patterns. This slice creates no
synthetic prompts, provider proxy, alternate base URL, or subscription-backed
API. Official permission for the existing direct OAuth quota endpoint is not
verified here; that pre-existing behavior is unchanged.
