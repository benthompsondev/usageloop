# UsageLoop desktop shell

UsageLoop is a compact Codex-only Windows app. The dashboard answers four
questions in order: is the reset clock running, when does it reset, how much of
the five-hour and weekly windows was last used, and what did UsageLoop do.

The interface uses a dark navy base, raised charcoal surfaces, thin borders,
emerald action colour, and short text-first state pills. The header says
**UsageLoop for Codex**. The loop mark uses a vector `5hr` glyph at normal app
sizes and a ring-only silhouette for tiny tray sizes. No installed font is
needed to render the mark.

The main switch is **Keep my 5-hour windows ready**. Dashboard copy keeps
the safeguards visible without exposing protocol details. Settings owns Codex
installation, five-hour evidence, weekly allowance, automation, local state,
Windows startup, diagnostics, and manual updates. About explains the mechanism,
privacy boundary, and independent-project disclaimer.

Countdowns advance locally once per second. Usage stays a last-known snapshot.
The GUI delegates all decisions to the hardened controller and provider core.
A runtime identity change schedules a lightweight capability probe only when
automation is enabled. A version string alone never pauses automation.

The update path is intentionally not self-modifying. A user starts the GitHub
Release check, UsageLoop downloads the exact installer and companion checksum,
verifies both, asks before launch, starts the normal installer, and exits.

Manual Sync is separate from automation. It takes four bounded, read-only
app-server observations and refreshes the five-hour and weekly snapshots. It
never selects a model or starts a Codex turn.
