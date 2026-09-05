# Building a release

## Windows

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

## Linux

The next shared release should be **1.4.0**: Linux support adds a product
capability after the immutable Windows v1.3.0 tag. Candidate archives from
`linux-1.3.0` must not be attached to that existing release. Keep the PR open
until the real automatic-start check passes and the candidate is accepted.

From a clean `main` checkout on an x86_64 Linux desktop:

```bash
python -m venv .venv
.venv/bin/python -m pip install --editable ".[build,test]"
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -q
PYTHON=.venv/bin/python ./scripts/build-linux.sh
```

The build must produce:

```text
dist/linux/UsageLoop-<version>-linux-x86_64/
dist/UsageLoop-<version>-linux-x86_64.tar.gz
dist/UsageLoop-<version>-linux-x86_64.tar.gz.sha256
```

The archive is byte-for-byte reproducible from the same commit. `SOURCE_DATE_EPOCH`
defaults to the author date of HEAD and pins the timestamps PyInstaller and tar
would otherwise vary, so building twice gives the same checksum. Confirm that before
publishing, because a checksum nobody can reproduce is not worth much:

```bash
PYTHON=.venv/bin/python ./scripts/build-linux.sh
sha256sum dist/UsageLoop-*-linux-x86_64.tar.gz
rm -rf dist build
PYTHON=.venv/bin/python ./scripts/build-linux.sh
sha256sum dist/UsageLoop-*-linux-x86_64.tar.gz
```

Both runs must print the same hash. A different commit gives a different hash,
which is expected.

Then, on a machine with Codex installed and signed in:

1. Extract the archive and run `./UsageLoop/UsageLoop` directly.
2. Confirm the Dashboard detects Codex and shows a real reset clock after
   pressing **Sync usage**. Sync is read-only and spends no quota.
3. Run `./install.sh`, launch from the application menu, then
   `./install.sh --uninstall` and confirm the launcher is gone and the state
   directory is untouched.
4. Check the tray on at least one desktop that has one and one that does not.
   Without a tray the window must stay the visible surface and closing it must
   exit rather than hide.
5. Turn the startup toggle on and off and confirm
   `${XDG_CONFIG_HOME:-~/.config}/autostart/usageloop.desktop` appears and
   disappears.

Refresh the Linux screenshots when the Settings or About pages change:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/capture-ui-screenshots.py \
  --platform Linux docs/screenshots
```

They use the same synthetic fixture as the Windows set and contact nothing.

ARM64 is not built. Do not advertise a Linux ARM64 download until one exists.

For a release:

1. Match the version in `src/sentinel/product.py` and `pyproject.toml`.
2. Never publish different bits under a version that has already been installed.
   Give local updater-test candidates a distinct prerelease or development version,
   or advance to the next patch version before publishing the final build.
3. Tag the verified commit as `vX.Y.Z`.
4. Attach each platform's artifact and its checksum without renaming either.
5. Install the uploaded artifacts on a clean machine of each platform shipped.
6. Confirm `/releases/latest` returns the tag and every exact asset.
7. Check the live README and website download, checksum, and release-note links.
   Stable links should use `/releases/latest`; keep prereleases pinned
   separately. Refresh the screenshots used by those pages when the visible
   workflow changes, and check the installed Windows app's manual updater.

Write release notes for the person using the app. Lead with what changed for
them, then add the technical detail that makes the claim trustworthy.

Prefer:

> UsageLoop now keeps working safely if its local state files become unreadable.

Over:

> Treats unreadable trigger history as an integrity failure.

Keep the notes short, concrete, and honest. Mention safety behavior when it
matters, but do not make users translate internal state-machine language.

The Windows updater reads the latest GitHub Release for
`benthompsondev/usageloop`. It rejects missing assets, untrusted
download hosts, older versions, and checksum mismatches. Repository and asset
names are therefore compatibility contracts for already-installed copies. The
Linux build has no in-app updater, so its archive name may carry the version.
