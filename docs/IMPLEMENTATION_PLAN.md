# Phase 1 and Phase 2 Implementation Plan

**Goal:** Ship an observation-only CLI that classifies the approximately
five-hour Codex rate-limit window from repeated official app-server reads.

**Architecture:** A child-process transport owns newline-delimited JSON I/O.
A protocol client owns initialization and the one allowed account request.
Pure quota and classifier modules turn fixture data into deterministic results.
The CLI owns orchestration, display, reconnects, and allowlisted local history.

**Tech stack:** Python 3.11+ standard library, `unittest`, PowerShell setup.

**Spec:** [`../PROJECT_SPEC.md`](../PROJECT_SPEC.md)

## File Map

- `src/sentinel/transport.py`: executable resolution and app-server lifecycle.
- `src/sentinel/protocol.py`: handshake, JSON-RPC correlation, sparse notifications.
- `src/sentinel/quota.py`: safe normalization and five-hour candidate selection.
- `src/sentinel/classifier.py`: conservative time-series state decision.
- `src/sentinel/history.py`: allowlisted JSONL persistence and recent-observation reads.
- `src/sentinel/cli.py`: `doctor`, `status`, `sample`, `watch`, and JSON rendering.
- `tests/fixtures/`: installed-protocol-shaped safe examples.
- `tests/test_*.py`: pure behavior and fake-process protocol tests.
- `scripts/setup.ps1`: local virtual environment and editable install.

## Task 1: Normalization and Classification

- [x] Write fixture-backed failing tests for anchored, sliding, jitter, absent,
  weekly-only, multiple buckets, malformed reset, duration changes, reset
  crossing, percentage changes, and explicit exhaustion.
- [x] Implement immutable safe observation records, duration-based selection,
  and a conservative slope/span classifier.
- [x] Run the focused tests and refactor only while they remain green.

## Task 2: App-Server Boundary

- [x] Write failing tests around a fake newline-JSON process for handshake,
  rate-limit response parsing, sparse notifications, auth errors, unavailable
  executables, timeouts, and clean shutdown.
- [x] Implement native Codex resolution, hidden child process management,
  request correlation, and sanitized error categories.
- [x] Prove that the protocol client emits only `initialize`, `initialized`, and
  `account/rateLimits/read`.

## Task 3: CLI, History, and Windows Setup

- [x] Write failing CLI/history tests for JSON shape, UNKNOWN before enough
  evidence, allowlisted logging, and safe error output.
- [x] Implement commands, progress output, 30-second sampling, polling watch,
  transition display, Ctrl+C shutdown, and reconnect behavior.
- [x] Add local setup/wrapper scripts and complete the repo guide and README.

## Task 4: Live Verification and Checkpoint

- [x] Run all unit tests, compilation, CLI help, and configured static checks.
- [x] Run live doctor, status JSON, and a default real sample against local Codex.
- [x] Scan source, logs, and diff for forbidden auth/private/prompt behavior.
- [x] Review the final diff against `PROJECT_SPEC.md`, then create a local commit.

## Validation

The build is accepted only when deterministic tests pass, the live handshake and
read succeed or yield an honestly documented environment blocker, and the source
scan shows no credential reads, private endpoint calls, or model-turn methods.

## Phase 2: One Codex Rollover Trigger

- [x] Inspect the accepted Phase 1 commit, installed native CLI/model catalog,
  official CLI reference, and current CCLimitPing trigger/changelog.
- [x] Define deterministic eligibility, weekly protection, reset buffer,
  persistent duplicate guard, bounded failure, and restart tests first.
- [x] Add a standard-library Windows ConPTY transport and an injectable
  interactive Codex trigger with no `exec` path or model escalation.
- [x] Add a one-shot coordinator that reports success only after Phase 1
  classifies the post-trigger samples as `ANCHORED`.
- [x] Run the full suite, live doctor, read-only sample, dry-run, safety scan,
  diff review, quality gate, and one local commit.
