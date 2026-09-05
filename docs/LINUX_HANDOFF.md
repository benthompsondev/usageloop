# Linux handoff

Where the Linux port actually stands, so the next person can go straight to
cross-platform verification instead of redoing the investigation.

Written after building and running UsageLoop 1.3.0 on Ubuntu 26.04, GNOME on
Wayland, x86_64, against a real signed-in Codex install.

## September 5 natural rollover: accepted

The installed 1.3.4 build recorded exactly one natural automatic start at
16:19:53 EDT on September 5, 2026, using `gpt-5.6-luna / low`. At 16:20:31,
post-trigger observations confirmed the fixed reset at 21:19:51. The attempt
followed reserved, launch attempted, possibly sent, and verified states without
a duplicate. Compatibility remains passed and the provider is Ready.

This closes the Linux automatic-start acceptance gap described below. No
runtime patch was needed after inspecting the successful run. Raw state and
journal evidence are preserved locally. See [1.3.4 validation](1.3.4-validation.md)
for the public release record. Older sections below are historical notes.

## September 5 refresh: 1.3.4 candidate (historical)

PR #1 now includes main v1.3.3 (`170c7e3`) and its existing **Recheck Codex
compatibility** action. The candidate version is **1.3.4**. This supersedes the
older version recommendation and automatic-start status below.

The installed Linux rollover failed before a trigger reservation. The saved
runtime was checked but did not match the compatible identity, leaving automation
unsupported even with the main switch enabled. Startup restores that failure,
and the scheduler stops before rollover. A later usage Sync replaced the failure
message with a fixed-reset message but left the compatibility gate closed.

The preserved journal contains observations only: no trigger reservations or
send records, and no observation at the missed boundary. The original probe
failure reason was not logged and its displayed detail was overwritten, so the
initial transport or capability failure remains unknown. The new fixed reset
seen later is not evidence of a UsageLoop start.

Recovery was tested against a byte-for-byte copy of the installed failure using
the inherited Recheck button and the real Linux app-server. Compatibility passed
and survived a reload. Automation stayed enabled, the journal's original bytes
were preserved, and trigger attempts stayed at zero. An RPC allowlist permitted
only initialization, usage reads, and model listing. Doctor passed with
`gpt-5.6-luna / low / standard service`. The installed app was left untouched.
Raw state, timestamps, hashes, the diagnostic harness, and its output are retained
locally rather than committed to the repository.

Cross-filesystem XDG adoption now stages a complete copy on the destination
filesystem, verifies it against the unchanged source, flushes copied files, then
renames the staged directory. The legacy folder remains a backup. Copy failure
keeps the legacy state authoritative; no partial target is published. Existing
XDG state is never merged with the old folder. A real separate-filesystem test
covers saved compatibility identities and a reserved trigger, alongside copy
failure and source-change regressions.

The full Linux suite passed: 530 tests run, 7 expected Windows-only skips.
The new regression recreates the saved compatibility mismatch with synthetic
values and proves Recheck itself does not start an action. Trigger selection,
idempotency, weekly checks, and standard-service policy are unchanged.

Before release, install the reviewed 1.3.4 candidate, use Recheck, confirm
Compatibility passed, and observe one natural eligible rollover outside an
active coding session. Recent starts must record one verified Luna/low attempt
with fixed-reset evidence. No real automatic Linux start has been proven by
this refresh. Keep PR #1 open; do not merge, tag, or publish yet.

## Branch and source (original port)

Branch `linux-1.3.0`, based on `main` at `9b61774`. The release candidate
combines the Linux port and hardening into one reviewed change. Original local
commits are preserved in a local backup branch. No tag or release is published.

There is no Linux fork. Platform differences live in shared modules, and
`src/sentinel/host.py` holds the few facts that genuinely differ.

The old `codex/linux-beta` branch is superseded. It forked at 1.0.8, twenty
releases back, and its PATH-only discovery does not find a Codex desktop
install. Treat it as history.

## How Codex is found, and why

This is the part worth not rediscovering.

On this machine the Codex desktop app is installed and signed in, and there is
**no `codex` on `PATH` at all**. A PATH-only search reports "Codex not found" on
a machine that obviously has Codex. That is what the old Linux beta did.

Two install locations exist, and they are the same file:

| Install | Path | sha256 |
| --- | --- | --- |
| Codex desktop app (`chatgpt` deb 26.901.41123) | `/usr/lib/chatgpt/resources/codex` | `f9d4eab2…29b8dc6` |
| Codex CLI managed app-server binary | `~/.codex/plugins/.plugin-appserver/codex` | `f9d4eab2…29b8dc6` |

Byte-identical, both `codex-cli 0.153.3`. So "desktop versus CLI" is not two
integrations, it is one integration with a discovery problem.

The desktop app runs that binary itself. From its live process table:

```
/usr/lib/chatgpt/resources/codex -c features.code_mode_host=true app-server …
```

The desktop app is located by following the launcher it puts on `PATH`, which
is the app's own rule rather than a guess at an install prefix:

```
/usr/bin/chatgpt -> ../lib/chatgpt/codex-launcher
$ cat /usr/lib/chatgpt/codex-launcher
#!/bin/sh
exec "$(dirname "$(readlink -f "$0")")/ChatGPT" "$@"
```

Resolving that symlink and taking its directory gives the install root, so
discovery works wherever the app is installed. `_LINUX_DESKTOP_PREFIXES` keeps
exactly one measured prefix, `/usr/lib/chatgpt`, for a session that starts with
a stripped `PATH`. Do not grow it with unverified paths.

Search order, highest first:

1. `USAGELOOP_CODEX_EXECUTABLE`, if it names a real file
2. `$CODEX_HOME/plugins/.plugin-appserver/codex` (default `~/.codex`)
3. A Codex desktop install, derived from its launcher on `PATH`
4. The one measured prefix
5. `codex` on `PATH`

2 through 4 are ranked newest-by-mtime, and duplicates are collapsed by resolved
path. That is the same rule Windows already used to avoid running a stale npm
shim: app-server behavior moves, so the newest installed binary wins.

## What the app-server actually returns here

Read firsthand over the documented stdio JSON-RPC. No UI was scraped and no
auth file was read.

The handshake reports Linux, `account/rateLimits/read` returns subscription
windows, and `model/list` provides the current available models. These probes
are read-only; they do not send a turn.

Identical from both install paths. `app-server --stdio` is accepted even though
`--help` does not list the flag.

## Runtime-proven on Linux

All of this was exercised on this machine against the real Codex, with
**zero Codex turns spent**. `history.trigger_attempts()` stayed at 0 throughout.

- Live desktop run, 40 of 40 checks: Codex detection, real reset clock and
  weekly allowance, Continuous / Once each day / Weekly routine, weekday and
  weekend split, Pause and Resume including the tray menu text and surviving a
  fresh state read, Recent starts, Manual Sync, close to tray, reopen, and
  state persistence under XDG.
- Packaged binary: discovers the same Codex the source build does, matching
  `runtime_identity`, and a second launch exits 0 while the first survives.
- No-tray fallback, 8 of 8: the window stays the visible surface, closing it
  exits rather than hiding into nothing, and Pause, Recent starts and Sync are
  all reachable from the Dashboard.
- Install and uninstall: `install.sh` writes a launcher that passes
  `desktop-file-validate`, uninstall removes the app, launcher and autostart
  entry, and leaves the state directory alone.
- Autostart: the entry passes `desktop-file-validate`, reconciles when the
  executable moves, and is removed when the preference is off.
- `sentinel doctor` end to end against the real app-server.
- Full suite: 482 passed, 7 skipped, 255 subtests. `unittest discover` runs 489.
- Windows-shaped regression checks from this Linux box: 19 of 19.

Manual Sync is `account/rateLimits/read` only. Turning automation on runs one
read-only capability probe. Neither can spend a turn.

## Candidate build

Build from the final committed tree with `PYTHON=.venv/bin/python
./scripts/build-linux.sh`. The archive and its `.sha256` file are written to
`dist/`. Record the candidate commit and checksum locally; do not reuse the
previous build's checksum after changing source.

The build pins `SOURCE_DATE_EPOCH` to the commit author date and sorts archive
members with fixed ownership. Two consecutive builds of the original port were
byte-identical.

## Not proven yet

Only things that are genuinely unverified. Everything above was run.

- **A real automatic start on Linux.** The five-hour window here was exhausted
  at 100% for the whole session, so the trigger path never fired end to end.
  This is the single most important gap. Everything up to the trigger is proven.
- **Windows on Windows.** Every Windows code path that can run without a
  Windows kernel was exercised from Linux and passes, but `winreg`,
  `CreateMutexW`, `GetFileVersionInfoW` and the Inno installer were not
  executed.
- **Desktops other than GNOME on Wayland.** The no-tray path was proven under
  Qt's offscreen platform, not on a real trayless session. KDE and XFCE are
  untested.
- **A machine with only one Codex install.** This box has both the desktop app
  and the CLI-managed binary. Neither the desktop-only nor the CLI-only case has
  been run on real hardware, though both are covered by tests.
- **`runtime_version` is null on Linux.** There is no Windows version resource
  to read. The compatibility guard keys on `runtime_identity`, which is size and
  mtime and works fine, so only the diagnostic summary is thinner. Reading the
  version from history would change persistence, which needs a decision first.
- **ARM64.** Nothing in the app blocks it. It has never been built or tested, so
  there is no artifact. `build-linux.sh` refuses unless
  `USAGELOOP_ALLOW_ANY_ARCH=1`.
- **A real update between two published releases.** The check, download,
  checksum verification, staging and the swap-and-relaunch were all proven on
  this desktop against the real 62 MB archive, but there is no published Linux
  release to update *from* yet. The first Linux release makes that testable.
- **The website.** `docs/index.html` is still Windows-only: its schema.org
  `operatingSystem`, the meta descriptions, the hero, and the download section
  all say Windows. It was left alone deliberately rather than half-updated.

## Updates on Linux

Settings has the same user-initiated flow Windows has: check GitHub on button
press, installed and available versions, concise notes, download, SHA-256
verification, install. The differences are the artifact and the last step.

- Windows looks for the fixed `UsageLoop-Setup.exe` pair. Linux looks for
  `UsageLoop-<version>-linux-x86_64.tar.gz` and its `.sha256`, whose names come
  from `product.linux_archive_name` so the build script and the updater cannot
  spell them differently. Publish both platforms' pairs in the same release.
- A release with only one platform's assets reports "no download yet" to the
  other, not an error. Every release published so far is Windows-only, so that
  is what a Linux user sees today.
- Installing reuses `install.sh`: the verified archive is unpacked under the
  app's own member validation, then the installer that shipped inside it waits
  for the app to exit, replaces the install, and relaunches. No root, nothing
  outside XDG directories.
- Only an install at `${XDG_DATA_HOME:-~/.local/share}/usageloop` is replaced in
  place. A copy running from an extracted tarball is shown the exact command
  instead, so an update cannot quietly create a second installation.

Two bugs here were found only by running it on a real desktop, not offscreen:
the installer was being invoked with `/bin/sh`, which is dash on Ubuntu and
mis-resolved its own directory; and the launcher was written against the
inherited `XDG_DATA_HOME` rather than the install prefix, so one run wrote a
launcher pointing at a different installation. Both are fixed and covered by
tests, and `install.sh` now re-execs itself under bash and refuses to remove an
existing install until the replacement is staged and runnable.

## What to run before release

On Windows:

1. `pwsh -NoProfile -File .\scripts\verify.ps1`
2. `pwsh -NoProfile -File .\scripts\build-windows.ps1`, then install for the
   current user on a clean VM. Confirm Settings still reads "Windows startup",
   the in-app updater still checks, and the tray still works.
3. Let the `windows-install-acceptance` matrix run. The upgrade chains from
   0.9.1 through 1.2.0 are the real regression net for the `app_data_root()`
   refactor, because they are what proves the one-shot state migration still
   adopts the pre-rebrand folder.

On Linux:

4. On a KDE or XFCE session: tray icon, and tray Pause, Recent starts and Quit.
5. On a GNOME session with no AppIndicator extension: confirm the window is the
   only surface and closing it exits.
6. On a machine with only the Codex CLI, and separately only the desktop app,
   confirm the Dashboard detects Codex.
7. `./install.sh`, reboot with the startup toggle on, confirm UsageLoop comes
   back, then `./install.sh --uninstall` and confirm
   `~/.local/state/usageloop` survives.
8. Let one real automatic start fire against a window that is not exhausted and
   confirm a `verified` entry appears in Recent starts.
9. Build twice and confirm the archive hash matches.

Both:

10. Capture Linux screenshots if Settings or About changed:
    `QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/capture-ui-screenshots.py --platform Linux docs/screenshots`
11. Decide on `USAGELOOP_CODEX_EXECUTABLE` and the one measured desktop prefix.
    Both are documented; neither is needed for a normal install.
12. Update `docs/index.html` for two platforms, or leave it Windows-only and say
    so on the release.

## Release recommendation

Ship Linux as a normal x86_64 release alongside Windows once check 8 passes.
Do not call it a beta. The old 1.1.0-beta.1 Linux artifact should be superseded
rather than kept as a parallel download, because its discovery is wrong for
anyone using the Codex desktop app.

Hold the release if check 8 has not been done. Everything else here is proven,
but an automatic start firing on Linux is the whole point of the app, and it is
the one thing an exhausted quota window prevented from being tested.

If you want a smaller first step, publish it as an x86_64 release with the
website left Windows-only and a plain line in the release notes saying Linux is
new and ARM64 is not built yet. That is honest and does not block on the site.

## Next release version

The reviewed candidate is **1.3.4**, as requested for this refresh. Build from
the recorded candidate commit and verify its checksum. Do not attach the archive
to an existing release or merge, tag, or publish until the natural Linux rollover
has passed and release approval is given.
