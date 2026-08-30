# Building a Windows release

UsageLoop has no published release yet. This is the checklist for the first one.

Nothing has ever been published from this repo, so there is no old installed
version in the wild and the asset rename from the previous product name costs
nothing. The first release must use the UsageLoop names below.

## Build and verify

From a clean `main` checkout on Windows:

```powershell
pwsh -NoProfile -File .\scripts\setup.ps1
pwsh -NoProfile -File .\scripts\verify.ps1
pwsh -NoProfile -File .\scripts\build-windows.ps1
```

The build must produce these exact files:

```text
dist\UsageLoop\UsageLoop.exe
dist\UsageLoop\UsageLoopStatus.exe
dist\UsageLoop-Setup.exe
dist\UsageLoop-Setup.exe.sha256
```

`UsageLoopStatus.exe` has to be inside `dist\UsageLoop\`. If it lands anywhere
else the installer will not ship it, and the uninstaller cannot remove the Claude
status line it registered.

Install the setup package for the current user, launch it from the Start Menu,
check the dashboard and tray, then uninstall it from Windows **Installed apps**.

## Publish

1. Confirm the version in `src/sentinel/product.py` matches `pyproject.toml`.
2. Tag the verified commit as `vX.Y.Z`.
3. Create a GitHub Release with short plain-language notes.
4. Attach both `UsageLoop-Setup.exe` and `UsageLoop-Setup.exe.sha256` without
   renaming either file.
5. Install from the uploaded artifact on a clean Windows machine before calling
   the release ready.

The in-app updater rejects a release if either file is missing, the version is
not newer, the download URL is outside the expected GitHub hosts, or the hash
does not match. Do not create a release from an unverified local build.

## How update discovery works

The app reads one URL:

```text
https://api.github.com/repos/benthompsondev/codex-window-sentinel/releases/latest
```

That is built from `github_owner` and `github_repo` in `src/sentinel/product.py`.
**Renaming the GitHub repository would break the updater for everyone already
installed**, which is why the repo slug stays `codex-window-sentinel` even though
the product is called UsageLoop.

The check only runs when a user presses **Check for updates**. A 404 means no
release has been published yet and is reported as "you are up to date", not as a
network error.

Asset names come from `installer_filename` and `checksum_filename` in the same
file. An installed copy looks for the names *it* was built with, so if those ever
change again, the transition release has to carry both the old and the new asset
names or older installs will report a missing installer.

## Testing an update from an older version

The updater only offers a release whose tag is strictly newer than the running
build, so this needs two builds:

1. Build and install the current version (0.7.0). Keep that installed.
2. Bump the version in `src/sentinel/product.py` and `pyproject.toml` to 0.7.1,
   commit, and build again.
3. Publish 0.7.1 as a GitHub Release with both 0.7.1 assets attached.
4. Open the installed 0.7.0 app, go to **Settings**, and press
   **Check for updates**. It should offer 0.7.1, download it, verify the
   SHA-256, ask before opening the installer, then exit so Windows can finish.

Checking for updates from the newest published version correctly reports that
there is nothing to install, so an update cannot be exercised with a single
release.
