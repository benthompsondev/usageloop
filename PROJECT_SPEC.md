# UsageLoop for Codex: product contract

## Outcome

Give a normal Windows user one understandable control: **Keep my 5-hour
windows ready**. The app shows the current five-hour clock, reset time,
five-hour usage, weekly safety, schedule, and last action without terminal
setup.

## Schedule semantics

- **Continuous** makes the existing guarded rollover eligible after the last
  verified reset plus its safety buffer.
- **Daily start time** makes it eligible at the first selected local time after
  that same boundary. It never starts early. A missed time remains due after
  sleep or restart, while a selected time that occurred during an active window
  rolls to the next day.
- Daylight-saving gaps normalize forward to a real local time. Repeated local
  times use one stable first occurrence.
- Scheduling never bypasses observation, weekly protection, atomic reservation,
  ambiguous-outcome guards, or post-trigger reset verification.

## Runtime flow

```text
local app-server observation
  -> eligibility and weekly checks
  -> atomic attempt reservation
  -> dynamic model selection
  -> ephemeral thread/start + turn/start
  -> bounded outcome handling
  -> authoritative post-trigger observation
  -> persisted verified or guarded state
```

## Consumer states

- `CLOCK RUNNING`
- `STARTING NEXT WINDOW`
- `WAITING FOR RESET`
- `AUTOMATION OFF`
- `NEEDS ATTENTION`

## Safety contract

- Four observations spanning at least 30 seconds are required for a
  quota-consuming desktop decision.
- A rollover uses the previously verified absolute reset plus a 15-second
  buffer. Bootstrap is explicit and uses a one-full-window cooldown.
- The official approximately seven-day Codex window must be unique and below
  99% used.
- Reservation and duplicate checks are cross-process and restart safe.
- Definite pre-submit failures may recover. Possibly submitted or ambiguous
  outcomes are permanently guarded for that opportunity.
- Turn lifecycle events are diagnostic. Only a newly anchored fixed reset is
  success.
- Automation off performs no compatibility probe, quota read, or trigger.
- Manual Sync is explicitly user-started, collects four read-only rate-limit
  observations, and never discovers models or starts a thread or turn.

## App and packaging

PySide6 provides the desktop window and tray. Background workers keep provider
work off the UI thread. PyInstaller builds a windowed onedir app and Inno Setup
builds a per-user installer. GitHub Release update checks are manual and
checksum-gated. Version and package names come from `sentinel.product`.

## Out of scope

Other providers, UI scraping, private APIs, credential reads, keepalives,
reset credits, telemetry, Windows services, Windows Task Scheduler jobs, silent
updates, admin requirements, and global PATH changes.

## Current proof boundary

The observer, app-server trigger transport, and live anchoring behavior have
each been proven. A packaged build has completed a genuine unattended rollover:
it observed an unanchored window, reserved once, sent the guarded app-server
turn, and verified a new fixed reset. The trigger payload remains unchanged for
1.0; the reliability changes only tighten missed-boundary and pre-submit crash
handling.
