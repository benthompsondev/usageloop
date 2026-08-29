# Project Spec: Codex Window Sentinel, Phases 1 and 2

## Goal

Provide the verified Codex foundation for a later consumer-friendly
window-chaining product. Phase 1 measures fixed versus sliding reset behavior.
Phase 2 sends one minimal normal interactive Codex request only after a proven
rollover, then reports success only if Phase 1 observes a fixed new reset.

## Why

- Problem: the displayed reset time alone cannot distinguish an anchored quota
  window from an inactive window whose reset time keeps moving forward.
- Skill: local process integration, JSON protocol handling, conservative time
  series classification, privacy-safe diagnostics, and deterministic testing.
- Portfolio story: a small local-first diagnostic tool that favors measurement
  and official interfaces over private endpoint shortcuts.

## Phase 2 Smallest Runnable Slice

- Observe four snapshots through `account/rateLimits/read`.
- Trigger only with `UNANCHORED` evidence, a recent known anchored boundary, a
  15-second reset buffer, an available weekly window below 99%, and no prior
  attempt for that boundary.
- Launch the base interactive Codex TUI under Windows ConPTY with a minimal
  initial message, `gpt-5.4-mini`, and `low` reasoning.
- Observe four more snapshots and report verified success only for `ANCHORED`.
- One command: `sentinel chain`, with `sentinel chain --dry-run` for inspection.

## Phase 2 Done Means

- [ ] `chain`, `chain --dry-run`, and `chain --json` use the existing observer.
- [ ] Weekly, reset-buffer, rollover-boundary, and persisted duplicate gates run
  before any trigger process starts.
- [ ] The trigger uses the interactive TUI, not `codex exec`, and does not read credentials.
- [ ] One failed or unverifiable attempt cannot trigger again at the same boundary.
- [ ] Safe JSONL trigger events exclude input and process output.
- [ ] Deterministic tests cover every requested trigger and restart path.
- [ ] Live verification does not manufacture a rollover or spend quota while anchored.

## Architecture and Data Flow

1. Locate a native Codex executable and launch `codex app-server --stdio`.
2. Complete `initialize` request and `initialized` notification handshake.
3. Send only `account/rateLimits/read` requests.
4. Normalize `rateLimitsByLimitId` when present, falling back to `rateLimits`.
5. Select an approximately 300-minute window by duration, then classify a
   sequence with explicit jitter tolerances and conservative ambiguity rules.
6. For `chain`, require conservative eligibility and persist a one-attempt
   reservation keyed by the previously anchored reset timestamp.
7. Launch the same installed Codex executable in its stable interactive mode
   through Windows ConPTY. Codex owns authentication and the model request.
8. Poll the app-server again and require fixed-reset evidence.
9. Render text or JSON and append only allowlisted fields to a user-local JSONL log.

`account/rateLimits/updated` is accepted as a sparse, opportunistic signal but
never replaces polling because a dedicated observation-only process does not
create the token-count events that normally carry that notification.

## Scope Boundaries

In scope: Phase 1 observation plus one Codex-only, post-rollover interactive
trigger and bounded verification attempt.

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
