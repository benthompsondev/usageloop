# Building a Windows release

From a clean `main` checkout:

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
`UsageLoopStatus.exe` helper if an earlier local alpha left it in the app folder,
and consolidates the retired `Window Sentinel` install directory into the
current `%LOCALAPPDATA%\Programs\UsageLoop` location. Codex and other provider
configuration is never changed.

For a release:

1. Match the version in `src/sentinel/product.py` and `pyproject.toml`.
2. Never publish different bits under a version that has already been installed.
   Give local updater-test candidates a distinct prerelease or development version,
   or advance to the next patch version before publishing the final build.
3. Tag the verified commit as `vX.Y.Z`.
4. Attach the installer and its checksum without renaming either.
5. Install the uploaded artifact on a clean Windows machine.
6. Confirm `/releases/latest` returns the tag and both exact assets.

Write release notes for the person using the app. Lead with what changed for
them, then add the technical detail that makes the claim trustworthy.

Prefer:

> UsageLoop now keeps working safely if its local state files become unreadable.

Over:

> Treats unreadable trigger history as an integrity failure.

Keep the notes short, concrete, and honest. Mention safety behavior when it
matters, but do not make users translate internal state-machine language.

The updater reads the latest GitHub Release for
`benthompsondev/usageloop`. It rejects missing assets, untrusted
download hosts, older versions, and checksum mismatches. Repository and asset
names are therefore compatibility contracts for already-installed copies.
