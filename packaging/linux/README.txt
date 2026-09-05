UsageLoop - Linux

Plan when your Codex day starts.

Requirements
------------
- An x86_64 Linux desktop with a graphical session (X11 or Wayland).
- Codex already installed and signed in: either the Codex desktop app or the
  Codex CLI. UsageLoop finds both on its own, and does not need `codex` on PATH.

Run it without installing
-------------------------
  ./UsageLoop/UsageLoop

Install for your user
---------------------
  ./install.sh

That copies the app to ${XDG_DATA_HOME:-~/.local/share}/usageloop and adds a
launcher to your application menu. It needs no root and changes no system
files or PATH. To remove it:

  ./install.sh --uninstall

Where your data lives
---------------------
Settings, schedule, and start history:
  ${XDG_STATE_HOME:-~/.local/state}/usageloop

Optional autostart entry, written only if you turn the setting on:
  ${XDG_CONFIG_HOME:-~/.config}/autostart/usageloop.desktop

Uninstalling leaves your settings and history in place. Delete that state
directory yourself if you want them gone.

Tray behavior
-------------
UsageLoop uses the system tray when your desktop provides one. GNOME does not
ship a tray by default; without an AppIndicator extension, UsageLoop runs as a
normal window instead and closing the window exits the app. Pause, Recent
starts, and Sync are all on the Dashboard, so nothing is tray-only.

If Codex is somewhere unusual
-----------------------------
UsageLoop checks $CODEX_HOME (default ~/.codex) for the binary the Codex CLI
manages, then follows the Codex desktop app's own launcher to wherever it is
installed, then falls back to `codex` on PATH.

You should not need to configure anything. If your Codex lives somewhere none of
that reaches, point at it directly:

  USAGELOOP_CODEX_EXECUTABLE=/path/to/codex ./UsageLoop/UsageLoop

Updates
-------
UsageLoop does not update itself on Linux. Download a newer build and run
install.sh again; your settings and history are kept.

Report a problem
----------------
https://github.com/benthompsondev/usageloop/issues/new?template=bug_report.yml
