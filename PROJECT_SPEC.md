# Project Spec: Window Sentinel

## Goal

Ship the verified Codex observer and guarded trigger as a simple per-user Windows
app. Phase 1 measures fixed versus sliding reset behavior. Phase 2 sends one
minimal subscription-backed app-server turn after a proven rollover or explicit
first-run bootstrap, then reports success only if Phase 1 observes a fixed new
reset. The desktop shell makes that core usable without a terminal.

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
- Resolve a model from `model/list`, then submit one ephemeral `thread/start`
  and one `turn/start` carrying a minimal text input.
- Observe four more snapshots and report verified success only for `ANCHORED`.
- Provide `sentinel chain` for known rollovers and explicit
  `sentinel bootstrap --confirm` for a first window with no historical boundary.
- Persist an attempt lifecycle that distinguishes reserved, launch-attempted,
  request-possibly-sent, verified, failed-recoverable, and failed-guarded states.

## Phase 2 Done Means

- [x] `chain`, `bootstrap`, dry-run, and JSON paths use the existing observer.
- [x] Weekly, reset-buffer, rollover-boundary, bootstrap, and duplicate gates run
  before any trigger process starts.
- [x] The installed native executable is preferred over an older PATH shim.
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
7. Submit the trigger on the same app-server connection used for observation.
   Codex owns authentication and the model request.
8. Poll the app-server again and require fixed-reset evidence.
9. Render text or JSON and append only allowlisted fields to a user-local JSONL log.

`account/rateLimits/updated` is accepted as a sparse, opportunistic signal but
never replaces polling because a dedicated observation-only process does not
create the token-count events that normally carry that notification.

## Windows App Scope

In scope: the existing Codex observer and guarded trigger, a polished PySide6
window and tray, local countdowns, opt-in per-user startup, background workers,
allowlisted Claude statusLine caching, one guarded prompt-free `--init-only`
operation, a manual checksum-gated GitHub Release updater, and a per-user
PyInstaller/Inno Setup package.

Out of scope: Claude prompts or model selection, keepalives, reset credits,
private endpoints, credential reads, API keys, UI scraping, silent
self-replacement, automatic/background update checks, telemetry, and global
PATH changes.

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
| 2026-08-29 | Python standard library only in the provider core, compatible with Python 3.11+ | Python 3.12 is absent locally; no system install is needed and current runtimes can run the utility. |
| 2026-08-29 | Installed schema plus current `openai/codex` source are authoritative | Protocol behavior is evolving and must not come from memory. |
| 2026-08-29 | Poll read requests; notifications are opportunistic | Upstream emits rate-limit updates from token-count events, which Sentinel intentionally does not create. |
| 2026-08-29 | Default sample is four observations at 10-second intervals | A 30-second baseline is clear at Unix-second resolution without an excessive wait. |
| 2026-08-29 | Phase 2 originally used interactive `codex [PROMPT]` under Windows ConPTY | Superseded 2026-08-30. See `docs/TUI_TRIGGER_POSTMORTEM.md`. |
| 2026-08-29 | Default trigger was `gpt-5.4-mini`, `low`, and a two-character message | Superseded 2026-08-30: the catalog now marks that model with `upgrade: gpt-5.6-luna`, which is what produced a blocking deprecation interstitial. Models are resolved dynamically instead. |
| 2026-08-29 | One attempt per anchored rollover boundary | Observer verification can detect failure, but it cannot prove an unverified request consumed zero quota. Retrying would risk duplicate spend. |
| 2026-08-29 | Bootstrap requires explicit confirmation, zero-percent high-confidence UNANCHORED evidence, and a full-window cooldown | A new user has no historical rollover to prove, so bootstrap uses current evidence without fabricating history. |
| 2026-08-29 | Definite pre-process failures are recoverable; any launch ambiguity is guarded | This preserves a real bootstrap opportunity without risking duplicate quota use. |
| 2026-08-29 | Override only the child `TERM`, and let Codex persist trust after exact prompt matching | Superseded 2026-08-30: the app-server path renders nothing and has no directory-trust concept. |
| 2026-08-30 | Trigger through app-server `thread/start` + `turn/start` instead of the TUI | A live experiment proved one app-server turn anchors the five-hour window: reset slope went from 1.004 with distance pinned near 17998s to a fixed timestamp within 1s across 62s. The methods are in the runtime's stable schema tier and reuse the observation connection, handshake, and Codex-owned auth. |
| 2026-08-30 | Resolve the model from `model/list` at request time; never persist a name | A non-null `upgrade` pointer is the machine-readable predictor of the deprecation interstitial that broke the previous trigger. |
| 2026-08-30 | Prefer the installed native executable over a PATH shim | The local npm shim measured five minor versions behind the native binary it shadowed, on the exact evolving surface Sentinel depends on. |
| 2026-08-30 | Treat JSON-RPC -32600/-32601/-32602 as definitely-not-sent | Observed directly: sending `allowProviderModelFallback` without the `experimentalApi` capability returned -32600 and started nothing. |
| 2026-08-30 | Turn lifecycle notifications are diagnostic only | An observer cannot infer quota accounting from a completion event; `ANCHORED` remains the only success criterion. |
| 2026-08-30 | Prefer advertised `low` reasoning and otherwise use the runtime default | The trigger should stay minimal without inventing an effort the selected model does not support. |
| 2026-08-30 | Serialize duplicate-check and reservation across Sentinel processes | Persisted idempotency is not sufficient if two processes can both check before either writes its reservation. The OS lock covers only that short transaction and releases on process exit. |
| 2026-08-30 | Treat malformed or unreadable attempt history as unsafe to trigger | An incomplete possibly-sent record must never look like a clean first run. |
| 2026-08-30 | Require one unique current default from `model/list` | Catalog ordering is not a safe model-selection signal when default metadata is missing or contradictory. |
| 2026-08-30 | Require the dedicated trigger workspace to be empty and non-redirected | A stale file, local instruction, link, or junction would violate the controlled-workspace boundary. |
| 2026-08-30 | Block cross-mode attempts within the same five-hour opportunity | Concurrent `chain` and `bootstrap` commands must not each reserve a turn merely because their mode names differ. |
| 2026-08-30 | Add a PySide6 thin shell without moving provider policy into the GUI | The hardened core remains authoritative while normal users get one understandable control, local countdowns, tray behavior, and a per-user installer. |
| 2026-08-30 | Treat provider version changes as capability-probe events, not automatic failures | Automation may continue when the required methods and semantics still pass the lightweight compatibility check. |
| 2026-08-30 | Keep updates manual and checksum-gated | A public GitHub Release check is separate from provider traffic. Sentinel downloads the per-user installer, verifies its companion SHA-256 file, asks for approval, launches setup, and exits instead of modifying itself. |
