# Project Spec: Codex Window Sentinel, Phases 1 and 2

## Goal

Provide the verified Codex foundation for a later consumer-friendly
window-chaining product. Phase 1 measures fixed versus sliding reset behavior.
Phase 2 sends one minimal normal interactive Codex request after either a proven
rollover or explicit first-run bootstrap eligibility, then reports success only
if Phase 1 observes a fixed new reset.

## Why

- Problem: the displayed reset time alone cannot distinguish an anchored quota
  window from an inactive window whose reset time keeps moving forward.
- Skill: local process integration, JSON protocol handling, conservative time
  series classification, privacy-safe diagnostics, and deterministic testing.
- Portfolio story: a small local-first diagnostic tool that favors measurement
  and official interfaces over private endpoint shortcuts.

## Phase 2 Smallest Runnable Slice

- Observe four snapshots over 30 seconds through `account/rateLimits/read`.
- Trigger only with `UNANCHORED` evidence, a recent known anchored boundary, a
  15-second reset buffer, an available weekly window below 99%, and no prior
  attempt for that boundary.
- Launch the base interactive Codex TUI under Windows ConPTY with a minimal
  initial message, `gpt-5.4-mini`, and `low` reasoning.
- Observe four more snapshots and report verified success only for `ANCHORED`.
- Provide `sentinel chain` for known rollovers and explicit
  `sentinel bootstrap --confirm` for a first window with no historical boundary.
- Persist an attempt lifecycle that distinguishes reserved, launch-attempted,
  request-possibly-sent, verified, failed-recoverable, and failed-guarded states.

## Phase 2 Done Means

- [x] `chain`, `bootstrap`, dry-run, and JSON paths use the existing observer.
- [x] Weekly, reset-buffer, rollover-boundary, bootstrap, and duplicate gates run
  before any trigger process starts.
- [x] Native executables and `.cmd` shims launch safely through interactive ConPTY.
- [x] Definite pre-launch failures recover; possibly sent requests cannot repeat.
- [x] Safe JSONL trigger events exclude input and process output.
- [x] Deterministic tests cover the trigger, bootstrap, and restart paths.
- [x] Live verification does not manufacture a rollover or spend quota while anchored.

## Architecture and Data Flow

1. Locate a native Codex executable and launch `codex app-server --stdio`.
2. Complete `initialize` request and `initialized` notification handshake.
3. Send only `account/rateLimits/read` requests.
4. Normalize `rateLimitsByLimitId` when present, falling back to `rateLimits`.
5. Select an approximately 300-minute window by duration, then classify a
   sequence with explicit jitter tolerances and conservative ambiguity rules.
6. Require high-confidence eligibility. Key `chain` to the previously anchored
   reset; give `bootstrap` an explicit idempotency key and full-window cooldown.
7. Launch the same installed Codex executable in its stable interactive mode
   through Windows ConPTY. Codex owns authentication and the model request.
8. Poll the app-server again and require fixed-reset evidence.
9. Render text or JSON and append only allowlisted fields to a user-local JSONL log.

`account/rateLimits/updated` is accepted as a sparse, opportunistic signal but
never replaces polling because a dedicated observation-only process does not
create the token-count events that normally carry that notification.

## Scope Boundaries

In scope: Phase 1 observation plus Codex-only, bounded rollover and explicit
first-window bootstrap trigger/verification paths.

Out of scope: Claude support, keepalives, reset credits, private endpoints,
credential reads, API keys, UI scraping, GUI/tray, startup tasks, schedulers,
installers, telemetry, public release work, and global PATH changes.

Later product direction, explicitly not part of this slice: Claude Code,
scheduling, packaging, and a consumer Windows interface.

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
| 2026-08-29 | Phase 2 uses interactive `codex [PROMPT]` under Windows ConPTY | Current CCLimitPing evidence shows `codex exec` can consume tokens without anchoring; the installed CLI documents the base command as the stable TUI. |
| 2026-08-29 | Default trigger is `gpt-5.4-mini`, `low`, and a two-character message | The installed native model catalog describes this available model as small and cost-efficient and confirms `low` support. No model fallback or escalation is allowed. |
| 2026-08-29 | One attempt per anchored rollover boundary | Observer verification can detect failure, but it cannot prove an unverified request consumed zero quota. Retrying would risk duplicate spend. |
| 2026-08-29 | Bootstrap requires explicit confirmation, zero-percent high-confidence UNANCHORED evidence, and a full-window cooldown | A new user has no historical rollover to prove, so bootstrap uses current evidence without fabricating history. |
| 2026-08-29 | Definite pre-process failures are recoverable; any launch ambiguity is guarded | This preserves a real bootstrap opportunity without risking duplicate quota use. |
| 2026-08-29 | Override only the child `TERM`, and let Codex persist trust only after exact matching of Sentinel's empty dedicated workspace | A real ConPTY should not inherit `TERM=dumb`; narrow TUI confirmation avoids trusting arbitrary repositories or relying on ephemeral config overrides. |
