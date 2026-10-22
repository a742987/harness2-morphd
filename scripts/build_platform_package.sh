#!/usr/bin/env bash
# Assemble a platform package from a PyInstaller output. Signing/notarization is done by CI.
set -euo pipefail
target="${1:?usage: build_platform_package.sh <os>-<arch> <engine-binary>}"
source_binary="${2:?missing engine binary}"
root="$(cd "$(dirname "$0")/.." && pwd)"
dest="$root/packages/platform/$target/bin"
mkdir -p "$dest"
case "$target" in
  win32-*) name="morphd-engine.exe" ;;
  *) name="morphd-engine" ;;
esac
install -m 0755 "$source_binary" "$dest/$name"
(
  cd "$root/packages/platform/$target"
  find bin -maxdepth 1 -type f -exec sha256sum {} \; | sort > SHA256SUMS
)
echo "assembled $target; now sign, verify, and publish with provenance."
