# UsageLoop

[![Latest release](https://img.shields.io/github/v/release/benthompsondev/usageloop?sort=semver)](https://github.com/benthompsondev/usageloop/releases/latest) [![Windows verification](https://github.com/benthompsondev/usageloop/actions/workflows/verify.yml/badge.svg)](https://github.com/benthompsondev/usageloop/actions/workflows/verify.yml) [![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Plan when your Codex day starts.**

For Codex accounts with a five-hour usage window, the next reset clock normally
starts with the first request after the previous window ends. Start at 7:00 AM
and the next reset is around noon. Start at 4:00 AM and it is around 9:00 AM.

UsageLoop can start that next window with one small Codex request, which uses
some of your subscription allowance. Pick one time, separate weekday and
weekend times, or a different time for every day of the week. It keeps windows
rolling through the day, pauses overnight, and shows you when the next one
begins.

UsageLoop does not add quota or bypass limits. It starts the normal next window
you were going to receive anyway, earlier. It is a local-first Windows app, with
a Linux beta available for testing.

**[Download for Windows (stable)](https://github.com/benthompsondev/usageloop/releases/latest/download/UsageLoop-Setup.exe)**
· **[Download for Linux (beta)](https://github.com/benthompsondev/usageloop/releases/tag/v1.1.0-beta.1)**
· [Windows release notes](https://github.com/benthompsondev/usageloop/releases/latest)
· [Report a problem](https://github.com/benthompsondev/usageloop/issues/new?template=bug_report.yml)

Requires Codex already installed and signed in. Your PC must be awake and
signed in, with UsageLoop running, for a scheduled start. The Windows installer
is unsigned; [check the download and SmartScreen guidance](#windows) before
running it. If your account has no five-hour window, this scheduling feature
does not apply.

![UsageLoop dashboard showing an active five-hour window and the next scheduled action](docs/screenshots/dashboard.png)

Screenshots show v1.3.3 with synthetic usage data.

- Native x64-compatible Windows app with a quiet system tray mode
- Free and open source under the MIT license
- No API key, telemetry, cloud account, or administrator rights
- Never reads Codex credentials, prompts, responses, or conversations
- Automation and Windows startup are off by default

[Website](https://benthompsondev.github.io/usageloop/) ·
[Set your weekly routine](#set-your-weekly-routine) · [Install](#install) ·
[See what it does](#what-usageloop-does) ·
[Review privacy and trust](#privacy-and-trust) · [Understand how it works](#how-it-works)
· [Get help](#get-help-and-share-feedback)

## Set your weekly routine

Most people do not want the same start time seven days a week. UsageLoop lets
you set a first-start time for weekdays and another for weekends, then override
any individual day if you want to.

![UsageLoop Weekly routine settings showing separate weekday and weekend first-start times, per-day overrides, and the Next routine card with first start, next reset, and overnight pause](docs/screenshots/settings-weekly-expanded.png)

- **Weekdays** and **Weekends** each hold one first-start time. Set the time,
  press **Apply Mon–Fri** or **Apply Sat–Sun**, and the button confirms with
  **Applied**.
- **Customize individual days** opens a row per day when you want Wednesday to
  differ from the rest of the week.
- **Next routine** tells you what is actually going to happen: the **first
  start**, the **next reset** five hours later, and when the **overnight pause**
  begins.

After the first start of the day, UsageLoop keeps windows rolling as each one
resets. It stops starting new windows in the evening so the next morning begins
on the time you chose rather than drifting later each day.

If you want something simpler, the other two choices are still there:
**Continuous** starts a new window as soon as the current one resets, and
**Once each day** uses a single daily start time.

### Take a day off without losing your routine

Choose **Pause until tomorrow** on the Dashboard, or right-click
the tray icon and choose **Pause until** the displayed date and time. This works
with **Weekly routine** and **Once each day**, not **Continuous**. The pause ends
at tomorrow's configured first-start time.

Automation stays enabled, but automatic starts wait until that exact time.
Your routine stays unchanged, and the pause survives restarting the app or PC.
Changing your schedule does not move an existing pause. **Resume automation**
ends it early; turning Automation off cancels it and leaves automation off.
Pause is unavailable while an operation is already running.

At the resume time, the normal scheduler and safety checks take over. It is not
a promise that a request will be sent at that moment.

### Check what happened while you were away

Open **Recent starts** from the Dashboard or tray to see the last ten window-start
attempts. Each entry shows when it happened, whether it was automatic or a manual
first start, and whether Codex confirmed the new reset clock.

![Recent starts showing a confirmed start and an earlier unconfirmed attempt](docs/screenshots/recent-starts.png)

An uncertain outcome stays **Not confirmed**, with an explanation of why
UsageLoop will not retry it. **Not sent** means no start request reached Codex.
**Refresh history** only rereads local records; it does not sync usage or send a
request. Your existing history is available after upgrading.

This is a record of start attempts, not a list of missed appointments. It does
not invent entries for pauses, Sync, or time when the app was closed or the PC
was asleep. A confirmed start does not guarantee that quota is still available.

## Install

### Windows

You need:

- an x64-compatible Windows PC;
- the Codex desktop app or Codex CLI installed and already signed in.

Download the latest stable per-user installer and its matching checksum:

- [UsageLoop-Setup.exe](https://github.com/benthompsondev/usageloop/releases/latest/download/UsageLoop-Setup.exe)
- [UsageLoop-Setup.exe.sha256](https://github.com/benthompsondev/usageloop/releases/latest/download/UsageLoop-Setup.exe.sha256)

The installer does not need administrator rights or change the global PATH.
Already installed? Open **Settings**, scroll to **Updates**, and choose
**Check for updates**. Checks and installation are manual; UsageLoop does not
update itself in the background.

> [!IMPORTANT]
> The current installer is not code-signed. Windows Defender SmartScreen may
> show **Windows protected your PC** or **Unknown publisher**. Download it only
> from this repository's Releases page and verify the SHA-256 checksum before
> continuing. If your organization blocks unsigned apps, do not bypass that
> policy.

To verify the two downloaded files in PowerShell:

```powershell
$expected = (Get-Content .\UsageLoop-Setup.exe.sha256).Split()[0]
$actual = (Get-FileHash .\UsageLoop-Setup.exe -Algorithm SHA256).Hash.ToLower()
$actual -eq $expected
```

The result must be `True`. After checking the source and checksum, Windows
normally exposes **More info → Run anyway** on systems that permit unsigned
apps.

### Linux Beta

The first Linux build is an x86_64 preview, not a stable Linux release. It
requires the signed-in Codex CLI to be available as `codex` on `PATH`.

Download both files:

- [UsageLoop-Linux-x86_64.tar.gz](https://github.com/benthompsondev/usageloop/releases/download/v1.1.0-beta.1/UsageLoop-Linux-x86_64.tar.gz)
- [UsageLoop-Linux-x86_64.tar.gz.sha256](https://github.com/benthompsondev/usageloop/releases/download/v1.1.0-beta.1/UsageLoop-Linux-x86_64.tar.gz.sha256)

Then verify, extract, and launch it:

```bash
sha256sum -c UsageLoop-Linux-x86_64.tar.gz.sha256
tar -xzf UsageLoop-Linux-x86_64.tar.gz
./UsageLoop-Linux-x86_64/UsageLoop/UsageLoop
```

Automatic updates are not available in this beta. Tray behavior may vary by
desktop environment; UsageLoop keeps its main window open when a tray is not
available.

## What UsageLoop does

On accounts that expose a five-hour window, its clock normally starts when you
use Codex. If the current window ends while you're away, the next reset clock
normally waits until you come back and use Codex again.

UsageLoop can start that next window for you with one minimal request. Your
current window ends at 1:00 AM. UsageLoop starts the next one at 4:00 AM. When
you sit down at 7:00 AM, that reset clock has already been running for three
hours, so your next full reset arrives around 9:00 AM instead of noon.

You can still work at 7:00 AM in either case if you have allowance left. The
benefit is an earlier next reset if you use up that window, not extra quota or
five hours of continuous model work. The weekly limit still applies. OpenAI
controls these limits, and account behavior can change; check the limits your
Codex app actually shows.

The dashboard shows:

- whether the five-hour reset clock is running;
- the local countdown and absolute reset time;
- the last-known five-hour usage percentage;
- the last-known weekly usage and safety state;
- the next scheduled action and the last automatic action UsageLoop took.

Automation has three local schedule choices:

- **Continuous** starts the next window after the reset and safety buffer, so
  windows roll continuously.
- **Once each day** waits until your chosen local time after a window ends.
- **Weekly routine** uses a first-start time per day, with quick weekday and
  weekend groups and optional per-day overrides. Windows keep rolling after the
  first start and pause overnight so the next day begins on schedule. See
  [Set your weekly routine](#set-your-weekly-routine).

All three run on your PC's local clock. If the PC is asleep at the selected
time, UsageLoop catches up once after wake or restart. If a Codex window is
still active, it waits for the next scheduled time rather than starting early.
Daylight-saving changes use the Windows local clock.

The countdown moves locally. It does not poll Codex to make the UI look live.
Usage percentages are snapshots from the last real observation.

**Sync usage** is the exception you control. Pressing it takes four read-only
Codex observations over about 30 seconds, then refreshes the five-hour and
weekly snapshots. It never selects a model or starts a Codex turn.

## First run

1. Open UsageLoop and confirm Codex is detected.
2. Review the cached five-hour and weekly state.
3. In Settings, choose **Continuous**, **Once each day**, or **Weekly routine**,
   then turn on **Keep my 5-hour windows ready**. Weekly routine gives each day
   its own first-start time: set the
   weekday and weekend times, then check the **Next routine** card before you
   leave.
4. On a true first run, choose **Start my first window now**. This explicit
   action uses the same evidence and weekly checks as an automatic start.
5. Leave UsageLoop in the tray. Automation and Windows startup stay off until
   you enable them.

Windows startup runs UsageLoop in your signed-in desktop session. If the PC
sleeps, the app catches up after wake. If it is powered off or you are signed
out, it catches up the next time you sign in. UsageLoop does not install a
Windows service or store your Windows password.

Settings contains the schedule, Windows startup, manual updates, and collapsed
technical diagnostics. Update checks contact GitHub only after a button click
and never affect quota.

## Privacy and trust

Codex owns authentication and network communication. UsageLoop launches the
installed `codex app-server` locally, but it does not:

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

The source is public, releases include a SHA-256 checksum, and the Windows test
and packaging workflow is visible in [GitHub Actions](https://github.com/benthompsondev/usageloop/actions/workflows/verify.yml).
The installer is still unsigned, so the checksum and public build process are
useful verification signals, not a substitute for a trusted code-signing
identity.

Suspected vulnerabilities can be reported privately through the
[security policy](SECURITY.md).

UsageLoop is an independent open-source project. It is not affiliated with,
endorsed by, or sponsored by OpenAI.

## How it works

UsageLoop completes the normal local app-server initialization handshake. It
reads subscription windows through `account/rateLimits/read`, identifies
windows by their actual duration, and classifies several observations instead
of trusting one timestamp.

When automation is enabled, a rollover start is allowed only after:

- four strong observations show the five-hour window is unanchored;
- the known reset boundary and 60-second safety buffer have passed;
- the official weekly Codex window is present and below 99% used;
- no attempt has already been reserved for that opportunity;
- the current Codex capabilities pass a lightweight compatibility probe.

The start uses one ephemeral `thread/start` plus one `turn/start` through the
same local app-server. UsageLoop prefers **GPT-5.6 Luna**, at the lowest reasoning
effort that the installed Codex catalog supports, with standard service instead
of Fast mode. The request asks for only “OK” and no tool use.

The model must be visible, support text, and have no retirement/upgrade hint.
If Luna is unavailable, GPT-5.4 mini is allowed only if it still meets those
checks. If neither qualifies, UsageLoop sends nothing. It never falls back to
Astra or Sol just because Codex recommends them as the general default, and it
does not assume that an unknown successor is cheap.

**Recent starts** shows the saved model and reasoning for each attempt.
`sentinel doctor` previews the live selection without sending a model request.
Codex controls usage accounting; a short prompt or lightweight model is not a
promise of a particular percentage. Success still requires fresh rate-limit
observations showing a fixed reset timestamp.

If a request may have been sent, UsageLoop never retries it automatically.

## Get help and share feedback

If a Codex update or temporary connection failure pauses compatibility, use
**Settings > Codex connection > Recheck Codex compatibility**. A shortcut also
appears on the Dashboard when a check is needed. Allow about 30 seconds. This
reads usage and supported models without sending a model request, changing your
routine, or retrying an uncertain start. If the check fails, fix the reported
problem and recheck when ready. UsageLoop does not repeatedly retry it for you.

- [Report a problem](https://github.com/benthompsondev/usageloop/issues/new?template=bug_report.yml)
- [Request a feature](https://github.com/benthompsondev/usageloop/issues/new?template=feature_request.yml)

Bug reports can include the privacy-safe summary from **Settings → Advanced →
Copy this summary**. Do not paste credentials, Codex prompts or responses,
conversations, account information, auth files, or unrelated logs.

UsageLoop helping you? Consider starring the repository. It helps other Codex
users find the project.

## Advanced use and development

<details>
<summary><strong>CLI and classifier reference</strong></summary>

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

Classifier states:

- `ANCHORED`: reset timestamp stays fixed while remaining time decreases.
- `UNANCHORED`: reset timestamp advances with wall time and stays about five
  hours away.
- `ABSENT`: no approximately five-hour window is exposed.
- `EXHAUSTED`: the relevant window explicitly reports a blocked state.
- `UNKNOWN`: evidence is insufficient, malformed, contradictory, or ambiguous.

False `UNKNOWN` is preferred over false certainty. Rate-limit semantics are
evolving implementation behavior and must be measured, not assumed.

</details>

## Local data and removal

State is stored under `%LOCALAPPDATA%\UsageLoop`. The installer removes its
per-user startup entry and installed files. To remove all remaining local data
after uninstalling:

```powershell
Remove-Item -LiteralPath "$env:LOCALAPPDATA\UsageLoop" -Recurse -Force
```

Only run that command if you want to discard saved reset evidence and trigger
reservations.

<details>
<summary><strong>Build from source</strong></summary>

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

For focused development checks:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -m compileall -q src tests
```

See [PROJECT_SPEC.md](PROJECT_SPEC.md) for the behavioral contract and
[docs/RELEASING.md](docs/RELEASING.md) for the release checklist.

</details>

## More of my work

The other local-first apps are [CloakScan](https://github.com/benthompsondev/cloakscan),
which strips secrets out of code and logs before you share them, and
[SignalSpace Finance](https://github.com/benthompsondev/ledger-local-finance),
private personal finance for Windows. My PowerShell automation portfolio is
[Enterprise PowerShell Systems](https://github.com/benthompsondev/enterprise-powershell-systems).

The rest is on [benthompsondev.github.io](https://benthompsondev.github.io/).
