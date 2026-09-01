#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${PYTHON:-python}"
archive_name="UsageLoop-Linux-x86_64.tar.gz"
bundle_name="UsageLoop-Linux-x86_64"

if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "The first Linux beta build supports x86_64 only." >&2
  exit 1
fi

cd "$repo_root"
rm -rf "$repo_root/dist/UsageLoop" "$repo_root/dist/linux"
rm -f "$repo_root/dist/$archive_name" "$repo_root/dist/$archive_name.sha256"

"$python_bin" -m PyInstaller --noconfirm --clean packaging/UsageLoop-linux.spec

bundle_root="$repo_root/dist/linux/$bundle_name"
mkdir -p "$bundle_root"
cp -a "$repo_root/dist/UsageLoop" "$bundle_root/UsageLoop"
cp "$repo_root/packaging/linux/README.txt" "$bundle_root/README.txt"
cp "$repo_root/LICENSE" "$bundle_root/LICENSE"

tar -czf "$repo_root/dist/$archive_name" -C "$repo_root/dist/linux" "$bundle_name"
(
  cd "$repo_root/dist"
  sha256sum "$archive_name" > "$archive_name.sha256"
)

echo "Archive: $repo_root/dist/$archive_name"
echo "Checksum: $repo_root/dist/$archive_name.sha256"
