#!/usr/bin/env bash
# Build the Linux x86_64 bundle. Mirrors scripts/build-windows.ps1 in shape:
# one PyInstaller COLLECT directory, archived with a checksum beside it.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${PYTHON:-python3}"

architecture="$(uname -m)"
if [[ "$architecture" != "x86_64" ]]; then
  # ARM64 is not blocked by anything in the app; it simply has not been built
  # or exercised yet. Set USAGELOOP_ALLOW_ANY_ARCH=1 to try it.
  if [[ "${USAGELOOP_ALLOW_ANY_ARCH:-0}" != "1" ]]; then
    echo "This build targets x86_64. Found $architecture." >&2
    echo "Set USAGELOOP_ALLOW_ANY_ARCH=1 to build it anyway (untested)." >&2
    exit 1
  fi
fi

# The updater looks for these exact asset names, so they come from the same
# place the updater reads them from rather than being spelled twice.
read -r version bundle_name archive_name <<<"$(
  "$python_bin" - "$repo_root" "$architecture" <<'PYTHON'
import sys
sys.path.insert(0, f"{sys.argv[1]}/src")
from sentinel.product import PRODUCT

architecture = sys.argv[2]
print(
    PRODUCT.version,
    PRODUCT.linux_bundle_name(PRODUCT.version, architecture),
    PRODUCT.linux_archive_name(PRODUCT.version, architecture),
)
PYTHON
)"

cd "$repo_root"

# A published checksum is only useful if the same tree rebuilds to the same
# bytes. PyInstaller stamps file times into base_library.zip and tar records a
# mtime per member, so both are pinned to one timestamp. It defaults to the
# author date of HEAD, not the commit date: rebasing or amending a commit
# rewrites the commit date and would silently change the artifact for a change
# that is otherwise identical.
if [[ -z "${SOURCE_DATE_EPOCH:-}" ]]; then
  SOURCE_DATE_EPOCH="$(git -C "$repo_root" log -1 --format=%at 2>/dev/null || true)"
  : "${SOURCE_DATE_EPOCH:=0}"
fi
export SOURCE_DATE_EPOCH
export PYTHONHASHSEED=0

rm -rf "dist/UsageLoop" "dist/linux" "build/UsageLoop" "build/UsageLoop-linux"
rm -f "dist/$archive_name" "dist/$archive_name.sha256"

"$python_bin" scripts/render_linux_icon.py
"$python_bin" -m PyInstaller --noconfirm --clean packaging/UsageLoop-linux.spec

bundle_root="dist/linux/$bundle_name"
mkdir -p "$bundle_root"
cp -a "dist/UsageLoop" "$bundle_root/UsageLoop"
cp "packaging/linux/README.txt" "$bundle_root/README.txt"
cp "scripts/install-linux.sh" "$bundle_root/install.sh"
cp "LICENSE" "$bundle_root/LICENSE"
cp "THIRD_PARTY_NOTICES.md" "$bundle_root/THIRD_PARTY_NOTICES.md"
chmod +x "$bundle_root/install.sh" "$bundle_root/UsageLoop/UsageLoop"

# --sort=name fixes member order, and the pinned mtime plus a numeric owner of
# 0 keeps the tar free of anything about the machine that built it. `gzip -n`
# omits the original name and timestamp from the gzip header.
find "dist/linux/$bundle_name" -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +
tar --sort=name \
    --mtime="@$SOURCE_DATE_EPOCH" \
    --owner=0 --group=0 --numeric-owner \
    --format=gnu \
    -cf - -C "dist/linux" "$bundle_name" | gzip -9n > "dist/$archive_name"
(cd dist && sha256sum "$archive_name" > "$archive_name.sha256")

echo
echo "Bundle:   $repo_root/$bundle_root"
echo "Archive:  $repo_root/dist/$archive_name"
echo "Checksum: $repo_root/dist/$archive_name.sha256"
