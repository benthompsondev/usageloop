# Weekly Routine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a seven-day first-window schedule with continuous daytime rollover and a derived five-hour overnight pause.

**Architecture:** Extend the pure local scheduler with a Weekly summary phase, persist one optional immutable seven-day schedule, and feed the resulting decision through the unchanged guarded rollover path. The PySide6 shell edits only local settings and renders previews from cached provider state.

**Tech Stack:** Python 3.12+, dataclasses, `zoneinfo`, PySide6, `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-01-weekly-routine-design.md`

## Global Constraints

- Preserve Continuous and Once each day behavior.
- Use the verified Codex reset as authority.
- Use a 60-second reset buffer and a five-hour protected interval.
- Never bypass provider weekly protection, reservations, or ambiguous-outcome guards.
- Perform no provider traffic in schedule or UI tests.

---

### Task 1: Pure weekly schedule calculation

**Files:**
- Modify: `src/sentinel/schedule.py`
- Test: `tests/test_schedule.py`

**Interfaces:**
- Produces: `WEEKLY`, validated weekly time tuples, weekly target helpers, and a `ScheduleSummary` phase usable by decisions and presentation.

- [x] Add failing literal-timestamp tests for weekday/weekend targets, Friday to Saturday, Sunday to Monday, pause boundaries, daytime rollover, active-window crossing, missed starts, DST, midnight, and noon.
- [x] Run `python -m unittest tests.test_schedule -v` and confirm failures identify missing Weekly behavior.
- [x] Implement target and pause calculations using existing `_local_candidate` normalization.
- [x] Run `python -m unittest tests.test_schedule -v` and confirm all schedule tests pass.

### Task 2: Persistence and controller migration

**Files:**
- Modify: `src/sentinel/app_state.py`
- Modify: `src/sentinel/app_controller.py`
- Test: `tests/test_app_state.py`
- Test: `tests/test_app_controller.py`

**Interfaces:**
- Consumes: Weekly scheduler interfaces from Task 1.
- Produces: `AppSettings.weekly_start_times`, atomic first initialization, individual and bulk setters, and weekly-aware automation decisions.

- [x] Add failing tests for seven-value round trips, old-state compatibility, one-time Daily seeding, invalid partial data, save-failure rollback, weekly guard preservation, automation off, restart catch-up, and schedule edits.
- [x] Run the focused state/controller tests and confirm the new assertions fail for missing behavior.
- [x] Add strict weekly schedule validation and serialization while leaving existing mode defaults unchanged.
- [x] Seed all seven values only when the first switch to Weekly succeeds; preserve saved values on later switches.
- [x] Pass the weekly schedule into `automation_decision` and keep every non-schedule guard before schedule evaluation.
- [x] Run the focused state/controller tests and confirm they pass.

### Task 3: Weekly Settings editor and truthful presentation

**Files:**
- Modify: `src/sentinel/desktop.py`
- Modify: `src/sentinel/ui_components.py`
- Modify: `src/sentinel/ui_theme.py` only if existing selectors cannot style the editor
- Test: `tests/test_desktop.py`
- Test: `tests/test_layout.py`

**Interfaces:**
- Consumes: persisted weekly values and `ScheduleSummary.phase`.
- Produces: weekday/weekend quick-set controls, seven individual time editors, local preview copy, Dashboard Next action, and tray text derived without provider calls.

- [x] Add failing UI tests for the three mode labels, quick-set behavior, individual edits, persistence rollback, preview copy, all Next action phases, and zero provider activity.
- [x] Run focused desktop/layout tests and confirm failures are caused by the absent Weekly editor.
- [x] Add the compact editor using existing setting-card and `QTimeEdit` patterns.
- [x] Render the approved explanation plus next-start, approximate-reset, and pause previews from local calculations.
- [x] Update Dashboard and tray presentation from the same schedule summary so wording cannot disagree.
- [x] Run focused desktop/layout tests and confirm they pass.

### Task 4: Safety integration and verification

**Files:**
- Test: `tests/test_desktop.py`
- Existing safety suites: `tests/test_chain.py`, `tests/test_outcome_recovery.py`
- Modify production files only if a failing integration test demonstrates a schedule-specific defect.

**Interfaces:**
- Consumes: weekly automation decisions.
- Verifies: the existing guarded rollover pipeline remains the only path to a request.

- [x] Add integration coverage showing a qualifying Weekly rollover reaches at most one in-process worker while a pause or automation-off decision reaches none; retain the existing cross-process reservation suites unchanged.
- [x] Run the focused integration test and confirm the guarded path is the only route to provider work.
- [x] Run all focused weekly suites.
- [x] Run `pwsh -NoProfile -File .\scripts\verify.ps1` and confirm zero failures.
- [x] Generate offscreen screenshots, inspect Settings and Dashboard at the supported compact layout, and run `git diff --check`.
- [x] Review the full diff against the spec and remove accidental complexity.
