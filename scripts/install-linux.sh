#!/usr/bin/env bash
# Per-user install of an extracted UsageLoop Linux bundle. No root, no PATH
# changes, nothing written outside the current user's XDG directories.
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage: install-linux.sh [--uninstall] [--prefix DIR]
                       [--wait-for-pid PID] [--relaunch]

Installs the bundle sitting beside this script into
  ${XDG_DATA_HOME:-~/.local/share}/usageloop
and adds a launcher to
  ${XDG_DATA_HOME:-~/.local/share}/applications

--uninstall removes both, plus the autostart entry. It leaves your settings and
start history in ${XDG_STATE_HOME:-~/.local/state}/usageloop untouched.

--wait-for-pid waits for a running UsageLoop to exit before replacing anything,
and --relaunch starts the new copy afterwards. UsageLoop's in-app updater passes
both; you do not need them when installing by hand.
USAGE
  exit 2
}

data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
config_home="${XDG_CONFIG_HOME:-$HOME/.config}"
prefix="$data_home/usageloop"
uninstall=0
wait_for_pid=""
relaunch=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --uninstall) uninstall=1; shift ;;
    --prefix) prefix="${2:-}"; [[ -n "$prefix" ]] || usage; shift 2 ;;
    --wait-for-pid) wait_for_pid="${2:-}"; [[ "$wait_for_pid" =~ ^[0-9]+$ ]] || usage; shift 2 ;;
    --relaunch) relaunch=1; shift ;;
    -h|--help) usage ;;
    *) echo "Unknown argument: $1" >&2; usage ;;
  esac
done

launcher="$data_home/applications/usageloop.desktop"
autostart="$config_home/autostart/usageloop.desktop"

if [[ "$uninstall" == "1" ]]; then
  rm -rf "$prefix"
  rm -f "$launcher" "$autostart"
  command -v update-desktop-database >/dev/null 2>&1 &&
    update-desktop-database "$data_home/applications" >/dev/null 2>&1 || true
  echo "Removed UsageLoop. Your settings and history remain in ${XDG_STATE_HOME:-$HOME/.local/state}/usageloop."
  exit 0
fi

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
payload="$here/UsageLoop"
if [[ ! -x "$payload/UsageLoop" ]]; then
  echo "No UsageLoop bundle found beside this script (expected $payload/UsageLoop)." >&2
  exit 1
fi

# Refuse to install a bundle over itself. Without this an in-app update that
# somehow staged into the install directory would delete its own source.
if [[ "$here" == "$prefix" || "$payload" -ef "$prefix" ]]; then
  echo "Refusing to install $here over itself." >&2
  exit 1
fi

if [[ -n "$wait_for_pid" ]]; then
  # The app asks for this when it updates itself, so nothing is replaced while
  # it is still running. Bounded: a stuck process must not strand the update.
  waited=0
  while kill -0 "$wait_for_pid" 2>/dev/null; do
    if (( waited >= 30 )); then
      echo "UsageLoop (pid $wait_for_pid) is still running after ${waited}s; not replacing it." >&2
      exit 1
    fi
    sleep 1
    waited=$((waited + 1))
  done
fi

# Replace atomically enough that a failed copy cannot leave a half-installed
# app the launcher already points at.
staging="$prefix.incoming.$$"
rm -rf "$staging"
mkdir -p "$(dirname "$prefix")"
cp -a "$payload" "$staging"
rm -rf "$prefix"
mv "$staging" "$prefix"

mkdir -p "$data_home/applications" "$data_home/icons/hicolor/256x256/apps"
[[ -f "$prefix/_internal/usageloop.png" ]] &&
  cp -f "$prefix/_internal/usageloop.png" "$data_home/icons/hicolor/256x256/apps/usageloop.png"

cat > "$launcher" <<DESKTOP
[Desktop Entry]
Type=Application
Name=UsageLoop
Comment=Keep your Codex reset clock running.
Exec="$prefix/UsageLoop"
Icon=usageloop
Terminal=false
Categories=Utility;
StartupWMClass=UsageLoop
DESKTOP
chmod 644 "$launcher"

command -v update-desktop-database >/dev/null 2>&1 &&
  update-desktop-database "$data_home/applications" >/dev/null 2>&1 || true

echo "Installed to $prefix"
echo "Launcher:    $launcher"
echo "Run it from your application menu, or: $prefix/UsageLoop"

if [[ "$relaunch" == "1" ]]; then
  echo "Relaunching $prefix/UsageLoop"
  setsid "$prefix/UsageLoop" >/dev/null 2>&1 < /dev/null &
fi
