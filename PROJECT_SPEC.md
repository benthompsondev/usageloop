# UsageLoop for Codex: product contract

## Outcome

Give a normal Windows user one understandable control: **Keep my Codex reset
clock running**. The app shows the current five-hour clock, reset time,
five-hour usage, weekly safety, and last action without terminal setup.

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
- `STARTING WINDOW`
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

## App and packaging

PySide6 provides the desktop window and tray. Background workers keep provider
work off the UI thread. PyInstaller builds a windowed onedir app and Inno Setup
builds a per-user installer. GitHub Release update checks are manual and
checksum-gated. Version and package names come from `sentinel.product`.

## Out of scope

Other providers, UI scraping, private APIs, credential reads, keepalives,
reset credits, telemetry, scheduled tasks, silent updates, admin requirements,
and global PATH changes.

## Current proof boundary

The observer, app-server trigger transport, and live anchoring behavior have
each been proven. The remaining release blocker is one genuine fresh-window
rollover/bootstrap run through the final packaged Codex-only app.
