# Window Sentinel Windows App

## Goal

Turn the hardened Sentinel backend into a per-user Windows app whose normal
workflow needs no terminal or configuration files. The product has one primary
control, **Keep my 5-hour windows ready**, and presents provider state in plain
language.

## Architecture

The PySide6 shell remains thin. `sentinel.app` owns window, tray, and background
worker lifecycles. `sentinel.providers` exposes narrow provider adapters that
return presentation-safe state and delegate quota decisions to the existing
backend. `sentinel.app_state` stores only UI preferences and cached display
state; the existing safe JSONL history remains authoritative for Codex
observations and trigger reservations.

Codex detection is local and inert. After explicit enablement, the adapter may
run the existing guarded bootstrap or rollover path. A provider version change
reruns capability discovery; it pauses automation only when required
capabilities are missing, ambiguous, or materially changed. Claude detection
and cached status are included, but automatic anchoring stays disabled until a
fresh-window experiment proves its exact minimal initialization operation.

Countdowns advance locally from the last verified absolute reset. Automation
off performs no app-server reads, Claude sessions, or model requests. Start
with Windows is an opt-in `HKCU` registration and defaults off.

## User flow

First launch shows a short privacy/safety introduction, detected provider cards,
and the global enable control. Enabling explains the bounded provider activity
before the first action. Codex can offer **Start my first window now** when no
verified reset history exists. Claude clearly reports that automatic readiness
is awaiting compatibility proof. Advanced diagnostics contain versions,
timestamps, and sanitized failure categories only.

Closing hides the app to the tray; **Quit Window Sentinel** exits it. The tray
opens/restores the same window and reflects whether automation is on or needs
attention.

## Packaging

PyInstaller builds a `--windowed` onedir application so no console appears.
Inno Setup wraps that directory into a non-admin, per-user installer with a
Start Menu shortcut and clean uninstall. The app remains functional without
Python installed.

## Implementation checklist

1. Add tested presentation state, settings, startup registration, and provider
   adapter contracts.
2. Add the Codex adapter around existing safe history and guarded coordinator;
   add a detection-only Claude adapter.
3. Add the PySide6 window, provider cards, tray lifecycle, local countdown, and
   background work boundary.
4. Add build scripts/specs for PyInstaller and Inno Setup.
5. Run backend and GUI tests, no-traffic safety checks, packaged launch checks,
   tray/restore checks, and visual passes at normal and 1366x768 sizes.

## Non-goals

No private endpoints, credential parsing, TUI parsing, updater service, public
release, Claude automatic anchoring, or speculative provider fallback.
