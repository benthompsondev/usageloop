# Codex Window Sentinel - Agent Instructions

Applies to Codex and Claude Code. Global rules come from Ben's homebase;
this file only adds what is specific to this repository.

## What This Repo Is

A Windows-first CLI that reads ChatGPT/Codex subscription rate-limit snapshots
through a local `codex app-server`. Phase 2 adds one bounded, normal interactive
Codex request after a proven rollover, followed by Phase 1 verification. See
`PROJECT_SPEC.md` for the contract.

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

# Verify without contacting OpenAI
pwsh -NoProfile -File .\scripts\verify.ps1
```

## Structure

- `src/sentinel/` - transport, protocol, normalization, classification, CLI.
- `tests/` - deterministic unit and fixture-based protocol tests.
- `scripts/` - local PowerShell setup and verification helpers.
- `docs/` - research and design evidence.

Phase 1 remains the observation foundation. Phase 2 is limited to the approved
Codex-only, one-shot trigger and verification loop.

## Boundaries

Always:

- Keep runtime behavior observation-only.
- Use only the local Codex app-server for account communication.
- Log allowlisted quota fields and sanitized error categories only.
- Identify a five-hour window by duration, never by `primary` position alone.
- Reserve at most one trigger attempt for each observed rollover boundary.
- Use the normal interactive Codex TUI through Windows ConPTY, then require
  Phase 1 to observe `ANCHORED` before reporting verified success.

Never:

- Read or parse `auth.json`, tokens, account IDs, email, prompts, or threads.
- Call private ChatGPT endpoints directly, including WHAM.
- Send app-server `thread/*`, `turn/*`, keepalive, or reset-credit requests.
- Use `codex exec`, private endpoints, or direct credentials to trigger a window.
- Log the trigger input, model output, or process output.
- Add telemetry, global PATH changes, admin requirements, or auto-start behavior.
- Add retries beyond the one reserved attempt for a rollover boundary.
- Push without Ben's explicit authorization and the required push workflow.

Ask Ben first:

- Any new runtime dependency, protocol method, security-boundary change beyond
  the approved Phase 2 interactive request, GUI,
  scheduled task, deployment, or publishing action.
