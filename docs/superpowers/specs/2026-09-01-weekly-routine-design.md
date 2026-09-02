# Weekly Routine Design

## Goal

Add a third schedule mode that lets a user choose the first-window time for
each weekday. After that first start, UsageLoop rolls windows continuously
until the five hours before the next day's selected start.

## Scheduling contract

Weekly routine is a local scheduling policy in front of the existing guarded
Codex rollover pipeline. It does not change provider observation, eligibility,
reservation, dispatch, or verification.

For the next scheduled local first start:

```text
pause_start = next_scheduled_start - 5 hours
protected interval = [pause_start, next_scheduled_start)
```

An eligible rollover inside that interval waits for the scheduled first start.
Outside the interval, Weekly routine behaves like Continuous mode and retains
the 60-second reset safety buffer. At or after the first-start time, a missed
start catches up once through the existing guarded rollover path.

A real anchored Codex window always wins. If its reset crosses a scheduled
first-start time, UsageLoop waits for that verified reset and does not send a
second request. Ambiguous dispatches remain guarded by the existing history.

Local wall-clock targets use the existing DST rules: nonexistent spring-forward
times normalize to the corresponding real time, and repeated fall-back times
use the first occurrence consistently.

## State and migration

`AppSettings` gains an optional immutable seven-entry schedule ordered Monday
through Sunday. Each entry is a validated `(hour, minute)` pair.

Missing weekly data means Weekly routine has never been initialized. The first
successful switch to Weekly routine atomically copies the existing valid Daily
time into all seven entries. Later mode switches preserve the saved weekly
values. Existing Continuous and Once each day settings retain their current
mode and behavior. Invalid or partial weekly data cannot produce an automatic
action.

## UI

Settings exposes three choices: Continuous, Once each day, and Weekly routine.
The weekly editor contains weekday and weekend quick-set controls plus seven
individual local-time controls. Persistence failure visibly restores the last
durable values.

Consumer copy explains that UsageLoop starts the first window near the selected
time, rolls windows during the day, and pauses overnight. A local preview shows
the next first start, its approximate five-hour reset, and the derived pause
start without promising exact provider timing.

Dashboard Next action distinguishes a scheduled first start, continuous daytime
rollover, overnight pause, an active window crossing the scheduled time, and a
missed-start catch-up. These calculations use cached state only.

## Safety and non-goals

- No separate stop time or persisted day-start marker.
- No change to Codex trigger input, model choice, weekly protection, or
  exactly-once history.
- Automation off still starts no provider operation.
- Tests submit no Codex model turn.
- No version bump, packaging release, push, or publication in this pass.
