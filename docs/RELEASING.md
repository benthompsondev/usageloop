# Building a Windows release

Window Sentinel is not published as a release yet. This is the checklist for
the first one when the functional testing is ready.

## Build and verify

From a clean `main` checkout on Windows:

```powershell
pwsh -NoProfile -File .\scripts\setup.ps1
pwsh -NoProfile -File .\scripts\verify.ps1
pwsh -NoProfile -File .\scripts\build-windows.ps1
```

The build must produce these exact files:

```text
dist\WindowSentinel\WindowSentinel.exe
dist\WindowSentinel-Setup.exe
dist\WindowSentinel-Setup.exe.sha256
```

Install the setup package for the current user, launch it from the Start Menu,
check the dashboard and tray, then uninstall it from Windows **Installed apps**.

## Publish later

1. Confirm the version in `src/sentinel/product.py` matches `pyproject.toml`.
2. Tag the verified commit as `vX.Y.Z`.
3. Create a GitHub Release with short plain-language notes.
4. Attach both `WindowSentinel-Setup.exe` and
   `WindowSentinel-Setup.exe.sha256` without renaming either file.
5. Install from the uploaded artifact on a clean Windows machine before calling
   the release ready.

The in-app updater rejects a release if either file is missing, the version is
not newer, the download URL is outside the expected GitHub hosts, or the hash
does not match. Do not create a release from an unverified local build.
