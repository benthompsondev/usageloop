# Building a Windows release

UsageLoop has no public release yet. From a clean `main` checkout:

```powershell
pwsh -NoProfile -File .\scripts\setup.ps1
pwsh -NoProfile -File .\scripts\verify.ps1
pwsh -NoProfile -File .\scripts\build-windows.ps1
```

The build must produce:

```text
dist\UsageLoop\UsageLoop.exe
dist\UsageLoop-Setup.exe
dist\UsageLoop-Setup.exe.sha256
```

Install for the current user, verify the dashboard and tray, then uninstall from
Windows Installed apps. The installer also removes the obsolete
`UsageLoopStatus.exe` helper if an earlier local alpha left it in the app folder.
The app itself removes only an exact UsageLoop-owned legacy status-line entry;
custom user configuration is never changed.

For a future release:

1. Match the version in `src/sentinel/product.py` and `pyproject.toml`.
2. Tag the verified commit as `vX.Y.Z`.
3. Attach the installer and its checksum without renaming either.
4. Install the uploaded artifact on a clean Windows machine.

The updater reads the latest GitHub Release for
`benthompsondev/codex-window-sentinel`. It rejects missing assets, untrusted
download hosts, older versions, and checksum mismatches. Repository and asset
names are therefore compatibility contracts for already-installed copies.
