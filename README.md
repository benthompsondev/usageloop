# Codex Window Sentinel

Codex Window Sentinel is the verified Codex foundation for a later
consumer-friendly window-chaining app.

Phase 1 observes the real ChatGPT/Codex subscription windows through the local
Codex app-server and classifies the approximately five-hour window as
`ANCHORED`, `UNANCHORED`, `ABSENT`, `EXHAUSTED`, or `UNKNOWN`.

Phase 2 adds one deliberately small product proof: after Sentinel has evidence
of a genuine rollover, `sentinel chain` can send one minimal request through
the normal interactive Codex CLI and then prove whether the new window actually
anchored.

## Boundaries

Sentinel does:

- use `account/rateLimits/read` through a local `codex app-server`;
- identify windows by reported duration instead of assuming `primary` means five hours;
- use several observations and conservative timestamp tolerances;
- run the stable interactive `codex [PROMPT]` TUI under Windows ConPTY for the
  one approved trigger;
- stop at one trigger attempt per observed rollover boundary;
- require post-trigger `ANCHORED` evidence before reporting verified success.

Sentinel does not:

- read `auth.json`, OAuth tokens, account IDs, email, or conversations;
- call WHAM or another private ChatGPT endpoint;
- use `codex exec`, an API key, UI scraping, or reset credits;
- log trigger input or model/process output;
- retry with another model;
- support Claude, scheduling, startup tasks, GUI/tray behavior, or packaging yet.

## Architecture and Privacy

```text
Observation:  Sentinel -> local codex app-server -> OpenAI
Trigger:      Sentinel -> Windows ConPTY -> interactive Codex CLI -> OpenAI
Verification: Sentinel -> local codex app-server -> OpenAI
```

Codex owns authentication and network communication on both paths. Sentinel
stores only allowlisted quota evidence and sanitized trigger events in:

```text
%LOCALAPPDATA%\CodexWindowSentinel\sentinel.jsonl
```

Trigger log records may contain the rollover timestamp, selected model,
reasoning level, sanitized outcome, and observed classifier state. They never
contain the two-character input, Codex output, credentials, or account data.

The interactive trigger timing strategy was adapted from the MIT-licensed
[CCLimitPing](https://github.com/wavever/CCLimitPing). See
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

## Requirements and Setup

- Windows 10 version 1809 or newer, or Windows 11, with ConPTY.
- Python 3.11 or newer. Python 3.12 is preferred but not required.
- A current installed Codex CLI/app signed into the intended ChatGPT subscription.

From this repository in PowerShell:

```powershell
pwsh -NoProfile -File .\scripts\setup.ps1
```

Setup creates only `.venv` inside the repository and installs Sentinel there.
It does not need administrator rights, change the global PATH, or install an
OpenAI API key.

## Commands

```powershell
.\sentinel.ps1 doctor
.\sentinel.ps1 status
.\sentinel.ps1 status --json
.\sentinel.ps1 sample
.\sentinel.ps1 watch
.\sentinel.ps1 chain --dry-run
.\sentinel.ps1 chain
.\sentinel.ps1 chain --json
```

- `doctor` verifies native Codex discovery, version, app-server handshake,
  subscription rate-limit availability, and Windows ConPTY availability. It
  never sends a model request.
- `status` adds one read-only observation and classifies recent safe history.
- `sample` takes four reads at 10-second intervals by default.
- `watch` polls every 30 seconds and reconnects after app-server failures.
- `chain --dry-run` applies every eligibility gate and reports the exact
  mechanism, model, reasoning level, input length, and retry bound without
  sending a request.
- `chain` takes four five-second preflight observations. Only proven
  `UNANCHORED` evidence can reach the trigger. It then takes four five-second
  verification observations and succeeds only if their reset timestamp is fixed.

The Phase 2 defaults are internal and injectable: `gpt-5.4-mini`, `low`
reasoning, and the two-character input `ok`. The installed native model catalog
described that model as small and cost-efficient and confirmed `low` support at
implementation time. Sentinel has no fallback or automatic escalation.

## Trigger Safety Gates

Before any request, `chain` requires all of the following:

1. Four observations classify the five-hour window as `UNANCHORED`.
2. Safe history contains a recent prior `ANCHORED` reset timestamp that is now past.
3. At least 15 seconds have passed since that reset.
4. A unique weekly window is exposed, below 99% used, and not explicitly blocked.
5. No trigger attempt is already recorded for that reset boundary.

Sentinel writes the attempt reservation before starting Codex. If the process
fails, exits successfully without anchoring, or Sentinel restarts, that boundary
does not get a second request. This is intentionally conservative because an
observer cannot prove that a failed-looking process consumed zero quota.

## Controlled Live Rollover Test

First run `sample` while the current window is anchored so its reset boundary is
in safe history. After the displayed reset time has passed by at least 20
seconds, run exactly:

```powershell
.\sentinel.ps1 chain
```

Run `chain --dry-run` at any time to inspect the decision without quota use. If
the real window is already anchored, `chain` reports `ALREADY_ANCHORED` and sends
nothing. Do not manufacture a rollover for testing.

## Classifier Semantics

- `ANCHORED`: at least three valid observations span 15 seconds and the absolute
  reset timestamp stays fixed within two seconds.
- `UNANCHORED`: the reset timestamp advances with wall time while its distance
  stays near one full reported window.
- `ABSENT`: no window near 300 minutes is exposed.
- `EXHAUSTED`: the selected bucket explicitly reports blocking or 100% use.
- `UNKNOWN`: evidence is insufficient, malformed, ambiguous, contradictory, or
  crosses a reset.

False `UNKNOWN` is preferred over a false anchored or unanchored result.

## Verify

Automated verification is deterministic and sends no model request:

```powershell
pwsh -NoProfile -File .\scripts\verify.ps1
```

Live read-only checks are explicit:

```powershell
.\sentinel.ps1 doctor
.\sentinel.ps1 sample
.\sentinel.ps1 chain --dry-run
```

## Troubleshooting

- `codex_not_found`: install/update Codex or ensure its native executable is available.
- `authentication_unavailable`: sign into Codex with the intended ChatGPT subscription.
- `interactive_tty_unavailable`: use supported Windows with ConPTY.
- `WEEKLY_UNAVAILABLE` or `WEEKLY_EXHAUSTED`: Sentinel refuses to consume quota.
- `ROLLOVER_BOUNDARY_UNKNOWN`: run `sample` during an anchored window, then retry
  only after that recorded reset.
- `ATTEMPT_ALREADY_RECORDED`: Sentinel will not spend a second request for that rollover.
- `ANCHOR_NOT_VERIFIED`: the request path ran, but evidence did not prove a fixed reset.
- `UNKNOWN`: collect a fresh `sample` and treat the state as undetermined.

Codex subscription rate-limit semantics and app-server payloads are evolving,
undocumented implementation behavior. Measure them rather than assuming a field
position, reset timestamp, model, or trigger path will remain valid.

## Remove Completely

Sentinel makes no PATH, startup, scheduled-task, or system-wide changes. Close
it, delete the project folder, and optionally remove its safe local state:

```powershell
Remove-Item -LiteralPath "$env:LOCALAPPDATA\CodexWindowSentinel" -Recurse
```
