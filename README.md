# UsageLoop for Codex

**Keep your Codex reset clock running.**

UsageLoop is a small Windows app for Codex subscribers. It shows whether the
current five-hour window is genuinely counting down, displays the last-known
five-hour and weekly usage, and can start the next window with one minimal
guarded Codex request.

It does not grant extra quota or change the limits on your plan. It starts the
normal next clock early so more of that five-hour period can pass before you
need Codex again.

UsageLoop is an independent open-source project. It is not affiliated with,
endorsed by, or sponsored by OpenAI.

## What the app shows

- whether the five-hour reset clock is running;
- the local countdown and absolute reset time;
- the last-known five-hour usage percentage;
- the last-known weekly usage and safety state;
- the last automatic action UsageLoop took.

The countdown moves locally. It does not poll Codex to make the UI look live.
Usage percentages are snapshots from the last real observation.

Automation has two local schedule modes:

- **Continuous** starts the next window after the current reset and safety
  buffer.
- **Daily start time** waits until your chosen local time after a window ends.
  If the PC is asleep at that time, UsageLoop catches up once after wake or
  restart. If a Codex window is still active at that time, it waits for the next
  day's selected time rather than starting early.

Daylight-saving changes use the Windows local clock. A missing spring-forward
time moves to the corresponding first real time; a repeated fall-back time uses
the first occurrence consistently.

**Sync usage** is the exception you control. Pressing it takes four read-only
Codex observations over about 30 seconds, then refreshes the five-hour and
weekly snapshots. It never selects a model or starts a Codex turn.

## How it works

UsageLoop launches the installed `codex app-server` locally and completes its
normal initialization handshake. It reads subscription windows through
`account/rateLimits/read`, identifies windows by their actual duration, and
classifies several observations instead of trusting one timestamp.

When automation is enabled, a rollover start is allowed only after:

- four strong observations show the five-hour window is unanchored;
- the known reset boundary and 60-second safety buffer have passed;
- the official weekly Codex window is present and below 99% used;
- no attempt has already been reserved for that opportunity;
- the current Codex capabilities pass a lightweight compatibility probe.

The start uses one ephemeral `thread/start` plus one `turn/start` through the
same local app-server. The selected model comes from the current `model/list`
catalog, with low reasoning when advertised. Success is reported only when
fresh rate-limit observations show a fixed reset timestamp.

If a request may have been sent, UsageLoop never retries it automatically.

## Privacy and security

Codex owns authentication and network communication. UsageLoop does not:

- read `auth.json`, tokens, credentials, email, or account identifiers;
- call private ChatGPT endpoints such as WHAM;
- scrape the Codex or ChatGPT UI;
- record prompts, model responses, conversations, or thread contents;
- send telemetry;
- require an API key or administrator rights.

Safe local history contains only timestamps, window duration, usage, reset
times, classifier evidence, attempt states, and sanitized error categories.

Automation and Windows startup are both off on a new install. While automation
is off, UsageLoop performs no provider-triggering work.

## Install and run

Download the current per-user installer from
[GitHub Releases](https://github.com/benthompsondev/usageloop/releases/latest).
The installer does not need administrator rights or change the global PATH.

To build it yourself on Windows:

```powershell
pwsh -NoProfile -File .\scripts\setup.ps1
pwsh -NoProfile -File .\scripts\verify.ps1
pwsh -NoProfile -File .\scripts\build-windows.ps1
```

The build creates:

```text
dist\UsageLoop\UsageLoop.exe
dist\UsageLoop-Setup.exe
dist\UsageLoop-Setup.exe.sha256
```

## Desktop flow

1. Open UsageLoop and confirm Codex is detected.
2. Review the cached five-hour and weekly state.
3. In Settings, choose **Continuous** or **Daily start time**, then turn on
   **Keep my 5-hour windows ready**.
4. On a true first run, choose **Start my first window now**. This explicit
   action is guarded by the same evidence and weekly checks.
5. Leave UsageLoop in the tray. The local countdown continues without Codex
   traffic between observations. Automation and Windows startup stay off until
   you enable them.

Windows startup runs UsageLoop in your signed-in desktop session. If the PC
sleeps, the app catches up after wake. If the PC is powered off or you are
signed out, it catches up the next time you sign in. UsageLoop does not install
a Windows service or store your Windows password.

Use **Sync usage** when the dashboard looks stale or Codex changes a limit
unexpectedly. Settings contains the schedule, Windows startup, manual updates,
and collapsed technical diagnostics. Update checks contact GitHub only after a
button click and never affect quota.

## CLI

The observer and guarded engine are also available from PowerShell:

```powershell
.\sentinel.ps1 doctor
.\sentinel.ps1 status --json
.\sentinel.ps1 sample
.\sentinel.ps1 watch
.\sentinel.ps1 chain --dry-run
.\sentinel.ps1 bootstrap --dry-run
```

`doctor`, `status`, and `sample` are read-only. A real bootstrap requires
`bootstrap --confirm`. The desktop app uses the same core.

## Classifier states

- `ANCHORED`: reset timestamp stays fixed while remaining time decreases.
- `UNANCHORED`: reset timestamp advances with wall time and stays about five
  hours away.
- `ABSENT`: no approximately five-hour window is exposed.
- `EXHAUSTED`: the relevant window explicitly reports a blocked state.
- `UNKNOWN`: evidence is insufficient, malformed, contradictory, or ambiguous.

False `UNKNOWN` is preferred over false certainty. Rate-limit semantics are
evolving implementation behavior and must be measured, not assumed.

## Local data and removal

State is stored under `%LOCALAPPDATA%\UsageLoop`. The installer removes its
per-user startup entry and installed files. To remove all remaining local data
after uninstalling:

```powershell
Remove-Item -LiteralPath "$env:LOCALAPPDATA\UsageLoop" -Recurse -Force
```

Only run that command if you want to discard saved reset evidence and trigger
reservations.

## Development

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -m compileall -q src tests
```

See [PROJECT_SPEC.md](PROJECT_SPEC.md) for the behavioral contract and
[docs/RELEASING.md](docs/RELEASING.md) for the release checklist.
