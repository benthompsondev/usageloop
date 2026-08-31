# UsageLoop for Codex - repository guide

This is a Windows-first PySide6 app and CLI for observing and safely starting
Codex subscription windows through the local `codex app-server`.

## Commands

```powershell
pwsh -NoProfile -File .\scripts\setup.ps1
.\sentinel.ps1 doctor
.\sentinel.ps1 status --json
.\sentinel.ps1 chain --dry-run
.\sentinel.ps1 bootstrap --dry-run
pwsh -NoProfile -File .\scripts\verify.ps1
pwsh -NoProfile -File .\scripts\build-windows.ps1
```

## Boundaries

- Observe through `account/rateLimits/read` on the local app-server.
- Identify the five-hour and weekly windows by duration, not slot names.
- Require high-confidence multi-sample evidence before any Codex turn.
- Persist reservations and never retry an ambiguous or possibly sent request.
- Check the weekly window before every start.
- Trigger only through ephemeral `thread/start` and `turn/start`, using the
  current `model/list` result and a dedicated empty workspace.
- Treat post-trigger fixed-reset evidence as authoritative.
- Keep countdowns local. Automation off means zero Codex-triggering activity.
- On a Codex version change, rerun the capability probe. Pause only when the
  required behavior is missing or ambiguous.
- Never read credentials, private endpoints, prompts, responses, or accounts.
- Never add TUI parsing or ConPTY. See `docs/TUI_TRIGGER_POSTMORTEM.md`.

The one-time migration in `legacy_cleanup.py` may remove only exact obsolete
UsageLoop-owned integration entries and files. Unknown user configuration fails
closed and stays untouched.

Ask before changing protocol methods, dependencies, persistence, security
boundaries, publishing, or deployment. A push requires explicit authorization.
