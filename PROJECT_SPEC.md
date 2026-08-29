# Project Spec: Codex Window Sentinel

## Goal

Provide the verified observation foundation for a later consumer-friendly
window-chaining product: a trustworthy Windows-first CLI that measures whether
the currently exposed approximately five-hour Codex subscription window has a
fixed reset timestamp or a reset timestamp that slides with wall-clock time.

## Why

- Problem: the displayed reset time alone cannot distinguish an anchored quota
  window from an inactive window whose reset time keeps moving forward.
- Skill: local process integration, JSON protocol handling, conservative time
  series classification, privacy-safe diagnostics, and deterministic testing.
- Portfolio story: a small local-first diagnostic tool that favors measurement
  and official interfaces over private endpoint shortcuts.

## Smallest Runnable Version

- Input: read-only `account/rateLimits/read` responses from a child
  `codex app-server` process.
- Output: current quota windows plus `ANCHORED`, `UNANCHORED`, `ABSENT`,
  `EXHAUSTED`, or `UNKNOWN`, with concise evidence and JSON output.
- One command: `sentinel sample` after local setup.

## Done Means

- [ ] `doctor`, `status`, `sample`, `watch`, and `status --json` work.
- [ ] Five-hour selection uses actual duration and preserves other windows.
- [ ] Multiple observations, not a single timestamp, drive anchored/unanchored results.
- [ ] Safe local JSONL logging contains no account, auth, prompt, or conversation data.
- [ ] Deterministic tests cover the requested classifier and protocol scenarios.
- [ ] Live doctor and sampling run against the installed Codex runtime without model turns.
- [ ] README explains what, why, run, verify, privacy, troubleshooting, and removal.

## Architecture and Data Flow

1. Locate a native Codex executable and launch `codex app-server --stdio`.
2. Complete `initialize` request and `initialized` notification handshake.
3. Send only `account/rateLimits/read` requests.
4. Normalize `rateLimitsByLimitId` when present, falling back to `rateLimits`.
5. Select an approximately 300-minute window by duration, then classify a
   sequence with explicit jitter tolerances and conservative ambiguity rules.
6. Render text or JSON and append only allowlisted fields to a user-local JSONL log.

`account/rateLimits/updated` is accepted as a sparse, opportunistic signal but
never replaces polling because a dedicated observation-only process does not
create the token-count events that normally carry that notification.

## Scope Boundaries

In scope: local CLI, app-server process management, read-only quota requests,
safe history, reconnect behavior, setup script, tests, and documentation.

Out of scope: prompts, keepalives, reset consumption, private endpoints,
credential reads, API keys, UI scraping, GUI/tray, startup tasks, schedulers,
telemetry, quota manipulation, and global PATH changes.

Later product direction, explicitly not part of Phase 1: minimal post-rollover
triggers, Claude Code observation/triggering, scheduling, verification loops,
and a consumer Windows interface. None of those may be inferred from or added
to this observer without a new approved design and security review.

Ask Ben first before any security-boundary, protocol-method, dependency,
publishing, deployment, or persistence expansion.

## Classification Contract

- `ANCHORED`: at least three valid observations spanning at least 15 seconds;
  reset timestamps remain fixed within jitter while remaining time falls.
- `UNANCHORED`: at least three valid observations spanning at least 15 seconds;
  reset timestamps advance approximately with wall time and reset distance stays
  close to the full declared window.
- `ABSENT`: the current snapshot exposes no approximately five-hour window.
- `EXHAUSTED`: a selected five-hour window or its bucket explicitly reports a
  reached/blocked state.
- `UNKNOWN`: insufficient, malformed, contradictory, changing, reset-crossing,
  or ambiguous evidence.

False `UNKNOWN` is preferable to a false anchored or unanchored result.

## Verification Plan

- Automated: standard-library unit tests, package compilation, and CLI help smoke test.
- Live: `sentinel doctor`, `sentinel status --json`, then the default real sample session.
- Safety: source/diff scan for forbidden auth paths, private endpoints, and turn/prompt methods.

## Privacy Check

- [x] No work-derived data, names, hostnames, or identifiers.
- [x] Fixtures use fake timestamps and quota identifiers only.
- [x] Runtime log schema excludes account identifiers, email, auth, prompts, and conversations.

## Decisions Log

| Date | Decision | Why |
| --- | --- | --- |
| 2026-08-29 | Python standard library only at runtime, compatible with Python 3.11+ | Python 3.12 is absent locally; no system install is needed and current runtimes can run the utility. |
| 2026-08-29 | Installed schema plus current `openai/codex` source are authoritative | Protocol behavior is evolving and must not come from memory. |
| 2026-08-29 | Poll read requests; notifications are opportunistic | Upstream emits rate-limit updates from token-count events, which Sentinel intentionally does not create. |
| 2026-08-29 | Default sample is four observations at 10-second intervals | A 30-second baseline is clear at Unix-second resolution without an excessive wait. |
