# Window Sentinel

Window Sentinel is a small Windows app that keeps subscription coding windows
ready without asking a normal user to manage terminals or config files. Its one
primary control is **Keep my 5-hour windows ready**.

![Window Sentinel dashboard](docs/screenshots/dashboard.png)

Phase 1 observes the real ChatGPT/Codex subscription windows through the local
Codex app-server and classifies the approximately five-hour window as
`ANCHORED`, `UNANCHORED`, `ABSENT`, `EXHAUSTED`, or `UNKNOWN`.

The guarded Codex provider handles the product proof. `sentinel chain` handles a proven
rollover, while the explicit `sentinel bootstrap --confirm` path can start a
first window when no historical anchored reset exists. Both paths allow one
minimal request through the local Codex app-server and report success
only when fresh observations prove that the window anchored.

The PySide6 desktop shell adds a focused dashboard, local countdowns,
background workers, a system tray, optional per-user startup, and manual update
checks through GitHub Releases. Claude is detected and can show cached status,
but automatic Claude anchoring remains deliberately disabled until its exact
minimal fresh-window operation is proven.

## Boundaries

Sentinel does:

- use `account/rateLimits/read` through a local `codex app-server`;
- identify windows by reported duration instead of assuming `primary` means five hours;
- use several observations and conservative timestamp tolerances;
- submit the one approved trigger as an ephemeral `thread/start` plus a single
  `turn/start` on that same app-server connection;
- launch native Codex executables directly and Windows `.cmd` shims through
  `cmd.exe`, never by passing a shim to `CreateProcessW`;
- persist reservation, launch, possibly-sent, verified, and recoverable/guarded
  failure states before deciding whether another request is safe;
- require post-trigger `ANCHORED` evidence before reporting verified success;
- rerun a lightweight capability probe when a provider binary changes, and
  continue only when required behavior remains compatible;
- advance visible countdowns locally without provider traffic;
- check GitHub Releases only when the user presses **Check for updates**, then
  verify the downloaded installer against its published SHA-256 checksum.

Sentinel does not:

- read `auth.json`, OAuth tokens, account IDs, email, or conversations;
- call WHAM or another private ChatGPT endpoint;
- use `codex exec`, an API key, UI scraping, or reset credits;
- log trigger input or model/process output;
- retry with another model;
- automatically anchor Claude, create scheduled tasks, silently replace its
  running executable, or opt the user into startup or automation.

## Architecture and Privacy

```text
Observation:  Sentinel -> local codex app-server -> OpenAI
Trigger:      Sentinel -> local codex app-server -> OpenAI
Verification: Sentinel -> local codex app-server -> OpenAI
Updates:      Sentinel -> public GitHub Releases (user initiated only)
```

Codex owns authentication and network communication on both paths. Sentinel
stores only allowlisted quota evidence and sanitized trigger events in:

```text
%LOCALAPPDATA%\CodexWindowSentinel\sentinel.jsonl
```

Trigger log records may contain a safe attempt identifier, trigger mode,
rollover timestamp when applicable, selected model, reasoning level, sanitized
process outcome, lifecycle state, and observed classifier state. They never
contain the two-character input, Codex output, credentials, or account data.

The original interactive trigger timing strategy was adapted from the MIT-licensed
[CCLimitPing](https://github.com/wavever/CCLimitPing). See
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

## Install and First Run

Normal users run `WindowSentinel-Setup.exe`. It installs for the current Windows
user, needs no administrator rights or separate Python installation, and adds a
Start Menu shortcut. On first launch:

1. Confirm that Codex and Claude Code are detected as expected.
2. Turn on **Keep my 5-hour windows ready** only if you want guarded provider
   automation. Leaving it off means zero provider-triggering activity.
3. If Codex has no historical anchored reset, choose **Start my first window
   now** and approve the explicit one-request bootstrap explanation.
4. Optionally enable **Start Window Sentinel with Windows** in **Settings**.

Closing the window keeps it in the system tray. Use **Quit Window Sentinel** in
the tray menu to exit completely.

The main window has three places:

- **Dashboard** shows the global control and the two provider cards.
- **Settings** holds startup, diagnostics, and the manual update check.
- **About** explains the safety boundary and links back to this repository.

## Source Requirements and Setup

- Windows 10 or Windows 11.
- Python 3.11 or newer for source development. The packaged app includes its
  runtime; Python 3.12 is preferred but not required.
- A current installed Codex CLI/app signed into the intended ChatGPT subscription.

From this repository in PowerShell:

```powershell
pwsh -NoProfile -File .\scripts\setup.ps1
```

Setup creates only `.venv` inside the repository and installs Sentinel there.
It does not need administrator rights, change the global PATH, or install an
OpenAI API key.

Open the desktop app from source with:

```powershell
.\.venv\Scripts\window-sentinel.exe
```

Build the runnable app and per-user installer with:

```powershell
pwsh -NoProfile -File .\scripts\build-windows.ps1
```

The CLI remains available for diagnostics and controlled provider testing.

## Updates

Update checking never runs at launch, on a timer, or in the background. When a
user presses **Check for updates**, Sentinel reads the latest public GitHub
Release. A usable Windows release must carry both exact files:

```text
WindowSentinel-Setup.exe
WindowSentinel-Setup.exe.sha256
```

Sentinel downloads the installer to the user's temporary folder, checks its
SHA-256 hash, asks before opening it, then checks the hash again immediately
before launch. It exits cleanly after the normal per-user installer starts
instead of replacing its own running files.
This source push does not create a GitHub Release. See
[`docs/RELEASING.md`](docs/RELEASING.md) for the later release checklist.

## CLI Commands

```powershell
.\sentinel.ps1 doctor
.\sentinel.ps1 status
.\sentinel.ps1 status --json
.\sentinel.ps1 sample
.\sentinel.ps1 watch
.\sentinel.ps1 chain --dry-run
.\sentinel.ps1 chain
.\sentinel.ps1 chain --json
.\sentinel.ps1 bootstrap --dry-run
.\sentinel.ps1 bootstrap --confirm
```

- `doctor` verifies native Codex discovery, version, app-server handshake,
  subscription rate-limit availability, and the model a trigger would resolve. It
  never sends a model request.
- `status` adds one read-only observation and classifies recent safe history.
- `sample` takes four reads at 10-second intervals by default.
- `watch` polls every 30 seconds and reconnects after app-server failures.
- `chain --dry-run` applies every eligibility gate and reports the exact
  mechanism, model, reasoning level, input length, and retry bound without
  sending a request.
- `chain` takes four observations over 30 seconds, matching the normal high
  confidence sample path. Only high-confidence `UNANCHORED` evidence can reach
  the trigger. It then takes the same strength of verification observations.
- `bootstrap --dry-run` checks first-window eligibility without a reservation or
  request. `bootstrap --confirm` is the explicit opt-in that may send one request.
  It additionally requires every five-hour observation to report zero percent
  used and applies a full 18,000-second cooldown after any possibly sent request.

Sentinel does not persist a model name. Immediately before every trigger it
reads `model/list` from the installed runtime and selects the visible default
model whose `upgrade` pointer is null. If the catalog has no unique current
default, Sentinel refuses to guess based on list ordering. It uses `low`
reasoning when the model advertises it, then falls back to the model's advertised
default. A model carrying an `upgrade` pointer has been superseded and is the
exact condition that produces a deprecation interstitial, so it is never selected.
If no model qualifies, Sentinel refuses to trigger rather than guess. The input
is the two-character message `ok`.

## Trigger Safety Gates

Before any request, both trigger paths require high-confidence `UNANCHORED`
evidence from four observations spanning at least 30 seconds and a unique weekly
window below 99% used and not blocked.

`chain` additionally requires:

1. Safe history contains a recent prior `ANCHORED` reset timestamp that is now past.
2. At least 15 seconds have passed since that reset.
3. No possibly sent request is already recorded for that reset boundary.

`bootstrap --confirm` instead requires explicit opt-in, zero percent five-hour
usage across the evidence set, and no possibly sent bootstrap request during the
previous full five-hour window. It does not invent a historical rollover.

The trigger thread is created with `ephemeral: true`, `sandbox: read-only`,
`approvalPolicy: never`, `config: {"mcp_servers": {}}`, and a `cwd` of Sentinel's
dedicated `%LOCALAPPDATA%\CodexWindowSentinel\trigger-workspace` directory, so the
request cannot load MCP servers, write files, request approvals, or persist a
thread. The workspace must be an empty real directory; Sentinel will not use a
link, junction, or directory containing local instructions or other files.
Sentinel opts out of `experimentalApi` and sends no parameter that
requires it. There is no directory-trust prompt on this path because the
app-server has no such concept.

Sentinel serializes the duplicate check and reservation across local processes,
and writes both the reservation and `launch_attempted` before releasing that
lock. Bootstrap and rollover attempts block each other within the same window.
Malformed or unreadable attempt history fails closed instead of appearing empty.
A rejection that Codex
emits before dispatching the request, which the JSON-RPC codes -32600, -32601,
and -32602 identify, becomes `failed_recoverable` and does not burn the
opportunity. Once `turn/start` has been transmitted by any other path, Sentinel
always performs read-only verification and blocks another request even when the
lifecycle outcome is ambiguous. A fresh bare reservation is treated as active
for two minutes, then becomes recoverable after a restart.

The bounded `turn/completed`, error, or timeout outcome is diagnostic only. A
completed turn is not success. The quota observer remains the sole authority for
anchoring.

## Controlled Live Rollover Test

First run `sample` while the current window is anchored so its reset boundary is
in safe history. After the displayed reset time has passed by at least 20
seconds, run exactly:

```powershell
.\sentinel.ps1 chain
```

Run `chain --dry-run` at any time to inspect the decision without quota use. If
the real window is already anchored, `chain` reports `ALREADY_ANCHORED` and sends
nothing. Do not manufacture a rollover for testing.

For the single controlled first-window proof on an untouched account, first
confirm that the intended Codex account is active, then run exactly:

```powershell
.\sentinel.ps1 bootstrap --confirm
```

Do not repeat it if Sentinel reports that a request was possibly sent, even when
anchoring could not be verified.

## Classifier Semantics

- `ANCHORED`: at least three valid observations span 15 seconds and the absolute
  reset timestamp stays fixed within two seconds.
- `UNANCHORED`: the reset timestamp advances with wall time while its distance
  stays near one full reported window.
- `ABSENT`: no window near 300 minutes is exposed.
- `EXHAUSTED`: the selected bucket explicitly reports blocking or 100% use.
- `UNKNOWN`: evidence is insufficient, malformed, ambiguous, contradictory, or
  crosses a reset.

False `UNKNOWN` is preferred over a false anchored or unanchored result.

## Verify

Automated verification is deterministic and sends no model request:

```powershell
pwsh -NoProfile -File .\scripts\verify.ps1
```

Live read-only checks are explicit:

```powershell
.\sentinel.ps1 doctor
.\sentinel.ps1 sample
.\sentinel.ps1 chain --dry-run
```

## Troubleshooting

- `codex_not_found`: install/update Codex or ensure its native executable is available.
- `authentication_unavailable`: sign into Codex with the intended ChatGPT subscription.
- `model_unavailable`: `model/list` failed or every visible model is superseded.
- `thread_start_rejected` or `turn_start_rejected`: Codex refused the request
  before dispatching it, so the opportunity stays recoverable.
- `WEEKLY_UNAVAILABLE` or `WEEKLY_EXHAUSTED`: Sentinel refuses to consume quota.
- `ROLLOVER_BOUNDARY_UNKNOWN`: run `sample` during an anchored window, then retry
  only after that recorded reset.
- `ATTEMPT_ALREADY_RECORDED`: Sentinel will not spend a second request for that rollover.
- `BOOTSTRAP_COOLDOWN`: a bootstrap request may already have been sent within one full window.
- `TRIGGER_NOT_SENT`: process creation definitely did not occur; the opportunity is recoverable.
- `VERIFICATION_UNAVAILABLE`: a request may have been sent, so Sentinel blocked a retry.
- `ANCHOR_NOT_VERIFIED`: the request path ran, but evidence did not prove a fixed reset.
- `UNKNOWN`: collect a fresh `sample` and treat the state as undetermined.

Codex subscription rate-limit semantics and app-server payloads are evolving,
undocumented implementation behavior. Measure them rather than assuming a field
position, reset timestamp, model, or trigger path will remain valid.

## Remove Completely

Use Windows **Installed apps** to uninstall Window Sentinel. The uninstaller
removes its per-user startup registration and installed files. Sentinel never
changes the global PATH or creates a scheduled task. To remove its safe local
history and preferences as well, delete:

```powershell
Remove-Item -LiteralPath "$env:LOCALAPPDATA\CodexWindowSentinel" -Recurse
```

For a source checkout, delete the project folder after quitting the app.
