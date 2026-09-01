UsageLoop 1.1.0 Beta 1 - Linux Preview

Target: x86_64 Linux desktop with a graphical session.

Requirements:
- Codex CLI installed, signed in, and available as `codex` on PATH
- `codex app-server` support in that CLI

Launch from the extracted folder:

  ./UsageLoop/UsageLoop

UsageLoop stores local evidence under XDG_STATE_HOME/usageloop, or
~/.local/state/usageloop when XDG_STATE_HOME is not set. Per-user autostart is
optional and writes ~/.config/autostart/usageloop.desktop unless
XDG_CONFIG_HOME is set.

This is the first Linux beta. Automatic updating is unavailable, and tray
behavior depends on the desktop environment. Windows users should continue
using stable v1.0.8.

Report a problem:
https://github.com/benthompsondev/usageloop/issues/new?template=bug_report.yml
