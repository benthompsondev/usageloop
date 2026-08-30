# UsageLoop (repo: codex-window-sentinel) - Agent Instructions

Applies to Codex and Claude Code. Global rules come from Ben's homebase;
this file only adds what is specific to this repository.

## What This Repo Is

A Windows-first desktop app and CLI that read ChatGPT/Codex subscription
rate-limit snapshots through a local `codex app-server`. The PySide6 shell keeps
the hardened observer and guarded trigger core intact. See `PROJECT_SPEC.md` and
`docs/WINDOWS_APP_DESIGN.md` for the contract.

## Commands

```powershell
# One-time local setup (creates only .venv inside this repo)
pwsh -NoProfile -File .\scripts\setup.ps1

# Run
.\sentinel.ps1 doctor
.\sentinel.ps1 status
.\sentinel.ps1 sample
.\sentinel.ps1 watch
.\sentinel.ps1 chain --dry-run
.\sentinel.ps1 bootstrap --dry-run

# Verify without contacting OpenAI
pwsh -NoProfile -File .\scripts\verify.ps1

# Build the windowed app and per-user installer
pwsh -NoProfile -File .\scripts\build-windows.ps1
```

## Structure

- `src/sentinel/` - transport, protocol, normalization, classification, CLI,
  provider adapters, and the PySide6 shell.
- `tests/` - deterministic unit and fixture-based protocol tests.
- `scripts/` - local PowerShell setup and verification helpers.
- `docs/` - research and design evidence.

Phase 1 remains the observation foundation. The desktop app exposes the approved
Codex rollover/bootstrap loop and a separately guarded Claude `--init-only` path.

## Boundaries

Always:

- Keep the app-server account/quota path observation-only; the only model request
  is the approved `turn/start` trigger after every safety gate passes.
- Use only the local Codex app-server for account communication.
- Log allowlisted quota fields and sanitized error categories only.
- Identify a five-hour window by duration, never by `primary` position alone.
- Require high-confidence four-sample evidence before any quota-consuming decision.
- Persist the trigger lifecycle; recover only definite pre-launch failures.
- Reserve at most one possibly sent trigger for each rollover boundary or
  five-hour bootstrap cooldown.
- Keep Claude isolated from the Codex transport. Claude automation may run only
  one installed-runtime `--init-only` operation, with no prompt or model flags,
  after cached five-hour and weekly safety evidence passes.
- Trigger with one ephemeral `thread/start` plus one `turn/start`, then require
  Phase 1 to observe `ANCHORED` before reporting verified success. Turn
  lifecycle notifications are diagnostic only and never a success verdict.
- Resolve the trigger model from `model/list` at request time. Never persist a
  model name, and never select a model carrying an `upgrade` pointer.
- Prefer the installed native Codex executable over an older PATH shim.
- Keep countdowns local between verified observations.
- Treat a provider version change as a reason to rerun the capability probe;
  pause only when that check fails, is ambiguous, or finds changed semantics.
- Keep automation off and Start with Windows off on first run.

Never:

- Read or parse `auth.json`, tokens, account IDs, email, prompts, or threads.
- Call private ChatGPT endpoints directly, including WHAM.
- Send app-server methods beyond the allowlist: `initialize`, `initialized`,
  `account/rateLimits/read`, `model/list`, `thread/start`, `turn/start`.
- Send keepalive, reset-credit, or any other account-mutating request.
- Parse rendered terminal output or automate the Codex TUI. See
  `docs/TUI_TRIGGER_POSTMORTEM.md`.
- Use `codex exec`, private endpoints, or direct credentials to trigger a window.
- Opt into `experimentalApi`, or send parameters that require it.
- Log the trigger input, model output, or thread contents.
- Add telemetry, global PATH changes, admin requirements, or automatic opt-in.
- Treat Claude process success as possibly effectful, not proof that the window
  anchored. A fresh statusLine observation remains authoritative.
- Retry when a request may already have been submitted.
- Push without Ben's explicit authorization and the required push workflow.

Ask Ben first:

- Any new runtime dependency, protocol method beyond the allowlist above,
  security-boundary change, scheduled task, deployment, or publishing action.
