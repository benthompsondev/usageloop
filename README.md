# Codex Window Sentinel

Codex Window Sentinel is the Phase 1 observation foundation for a later
consumer-friendly window-chaining product. Today it answers one narrow
question: is the approximately five-hour Codex subscription window actually
anchored to a fixed reset time, or is the reported reset time sliding forward?

It is a Windows-first CLI. It measures several read-only snapshots, identifies
the five-hour window by its reported duration, and makes a conservative call:
`ANCHORED`, `UNANCHORED`, `ABSENT`, `EXHAUSTED`, or `UNKNOWN`.

## What It Does Not Do

Phase 1 does not send prompts, start turns, trigger or keep windows alive,
consume reset credits, support Claude Code, schedule work, add startup tasks,
or provide a GUI/tray app. It never reads Codex credential files, handles OAuth
tokens, calls WHAM/private ChatGPT routes, scrapes UI, or sends telemetry.

## Architecture and Privacy

```text
Sentinel CLI -> local codex app-server -> OpenAI
```

Codex owns authentication and network communication. Sentinel sends the normal
app-server initialization handshake and only `account/rateLimits/read`.
It discards account metadata and keeps safe quota fields: observation time,
window duration, used percentage, reset timestamp, safe limit ID, classifier
state, and numeric evidence.

The JSONL log is stored at:

```text
%LOCALAPPDATA%\CodexWindowSentinel\sentinel.jsonl
```

It never contains tokens, email, account IDs, prompts, conversations, auth file
contents, or complete raw app-server messages. See [`docs/RESEARCH.md`](docs/RESEARCH.md)
for the verified protocol evidence.

## Requirements

- Windows 10/11.
- Python 3.11 or newer. Python 3.12 is preferred but not required.
- A current Codex CLI/app with `codex app-server` support.
- Codex signed in with the ChatGPT subscription whose windows you want to observe.

No administrator rights or OpenAI API key are required.

## Setup

From this repository in PowerShell:

```powershell
pwsh -NoProfile -File .\scripts\setup.ps1
```

This creates `.venv` inside the repository and installs Sentinel there. It does
not change the global PATH or install system-wide software.

## Commands

```powershell
.\sentinel.ps1 doctor
.\sentinel.ps1 status
.\sentinel.ps1 status --json
.\sentinel.ps1 sample
.\sentinel.ps1 watch
```

- `doctor` locates Codex, reports versions, completes the app-server handshake,
  and proves subscription rate limits are readable.
- `status` adds one observation and classifies up to four recent safe samples.
- `sample` takes four reads at 10-second intervals by default, then shows the
  evidence-based result. Use `--count` or `--interval` only for diagnostics.
- `watch` polls every 30 seconds by default, prints state transitions, and
  reconnects after app-server failures. Ctrl+C stops it cleanly.
- `status --json` emits stable machine-readable output for later automation.

After activating `.venv`, the same commands are available as `sentinel ...`.

## Classifier Semantics

- `ANCHORED`: at least three valid observations span 15 seconds or more and the
  absolute reset timestamp stays fixed within a two-second jitter tolerance.
- `UNANCHORED`: the reset timestamp advances with wall time, its distance stays
  near one full reported window, and pairwise movement is consistent.
- `ABSENT`: no window near 300 minutes is exposed.
- `EXHAUSTED`: the selected bucket explicitly reports blocking or 100% usage.
- `UNKNOWN`: evidence is insufficient, malformed, ambiguous, contradictory,
  crosses a reset, changes duration/bucket, or fits neither safe pattern.

False `UNKNOWN` is preferred over a false anchored/unanchored claim.

## Verify

Automated verification does not contact OpenAI:

```powershell
pwsh -NoProfile -File .\scripts\verify.ps1
```

Live verification is explicit:

```powershell
.\sentinel.ps1 doctor
.\sentinel.ps1 sample
```

## Troubleshooting

- `codex_not_found`: install/update Codex or ensure its native executable is on
  PATH. Sentinel prefers `codex.exe` over command shims.
- `authentication_unavailable`: sign into Codex with the intended ChatGPT
  subscription. API-key auth does not expose subscription windows.
- `protocol_unsupported`: update Sentinel's verified schema assumptions before
  trusting results from the newer Codex build.
- `UNKNOWN`: run `sample`; if it remains unknown, inspect only the safe JSONL
  evidence and treat the state as undetermined.

Codex rate-limit semantics are undocumented/evolving implementation behavior.
Measure them rather than assuming `primary` means five hours or that one reset
timestamp proves a window is anchored.

## Remove Completely

Sentinel makes no global PATH or startup changes. Close it, move outside the
repository, delete the project folder, and optionally delete its local log:

```powershell
Remove-Item -LiteralPath "$env:LOCALAPPDATA\CodexWindowSentinel" -Recurse
```

Deleting the repository also removes its `.venv` and command wrapper.
