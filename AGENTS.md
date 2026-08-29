# Codex Window Sentinel - Agent Instructions

Applies to Codex and Claude Code. Global rules come from Ben's homebase;
this file only adds what is specific to this repository.

## What This Repo Is

A Windows-first, observation-only CLI that reads ChatGPT/Codex subscription
rate-limit snapshots through a local `codex app-server`. See
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

# Verify without contacting OpenAI
pwsh -NoProfile -File .\scripts\verify.ps1
```

## Structure

- `src/sentinel/` - transport, protocol, normalization, classification, CLI.
- `tests/` - deterministic unit and fixture-based protocol tests.
- `scripts/` - local PowerShell setup and verification helpers.
- `docs/` - research and design evidence.

Phase 1 is infrastructure for a later window-chaining product, not permission
to implement triggers, Claude support, scheduling, or consumer UI in this repo.

## Boundaries

Always:

- Keep runtime behavior observation-only.
- Use only the local Codex app-server for account communication.
- Log allowlisted quota fields and sanitized error categories only.
- Identify a five-hour window by duration, never by `primary` position alone.

Never:

- Read or parse `auth.json`, tokens, account IDs, email, prompts, or threads.
- Call private ChatGPT endpoints directly, including WHAM.
- Send `thread/*`, `turn/*`, prompt, keepalive, reset-credit, or quota-triggering requests.
- Add telemetry, global PATH changes, admin requirements, or auto-start behavior.
- Push without Ben's explicit authorization and the required push workflow.

Ask Ben first:

- Any new runtime dependency, protocol method, security-boundary change, GUI,
  scheduled task, deployment, or publishing action.
