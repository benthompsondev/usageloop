# UsageLoop desktop shell

## Product shape

UsageLoop is a small per-user Windows app with one primary control:
**Keep my 5-hour windows ready**. The dashboard shows cached provider state in
plain language. Settings owns startup, diagnostics, and updates. About owns the
privacy boundary and project links.

The visual system follows the same family as CloakScan and SignalSpace Finance:
dark navy foundations, quiet raised surfaces, thin borders, a restrained green
action colour, compact spacing, and state pills that use both words and colour.
The identity is temporary. Product name, version, GitHub location, installer
names, and icon filename live in `sentinel.product` so a later rename stays
bounded.

## Boundaries

The PySide6 shell remains a client of the hardened core. Provider adapters
return presentation-safe state. `app_state` stores only UI preferences and
cached display state. Safe JSONL history remains authoritative for Codex
observations and trigger reservations.

Countdowns change locally once per second. Usage stays a last-known snapshot.
Automation off produces no provider-triggering work. A provider binary change
causes a lightweight capability probe only after automation is enabled; a
version string alone does not pause the provider.

Claude Code uses an isolated prompt-free `--init-only` operation. The card shows
Ready, Waiting, or Starting when the installed runtime and cached quota evidence
are usable. It shows **Automation paused** only when compatibility is unavailable
or ambiguous. Technical provider reasons stay in Diagnostics.

## Updates

Update work is separate from provider work. Nothing runs automatically. A user
click asks the GitHub Releases API for the latest public release. A release is
installable only when it contains the exact per-user installer and companion
SHA-256 file. Sentinel downloads both to a temporary version folder, verifies
the installer, asks for approval, verifies it again immediately before launch,
starts the normal installer, and exits.

This is intentionally not a self-modifying updater. There are no silent installs,
background checks, retries, or fallback download sources.

## Packaging

PyInstaller produces a windowed onedir app. Inno Setup wraps it in a no-admin,
per-user installer. `scripts/build-windows.ps1` reads `sentinel.product`, builds
both layers, and writes the checksum companion expected by the updater.

## Still out of scope

Scheduling, a final brand/name, telemetry, private provider endpoints,
credential reads, production releases, and update signing beyond the published
SHA-256 artifact contract.
