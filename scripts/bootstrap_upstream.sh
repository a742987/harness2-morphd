#!/bin/sh
# Clone the pinned upstream into vendor/harness2-src if the vendored copy is absent.
set -eu
commit=1cfe254e8d25
root=$(cd "$(dirname "$0")/.." && pwd)
dest="$root/vendor/harness2-src"
if [ -f "$dest/harness2/core/schedule.py" ]; then
  echo "upstream already present at $dest"
  exit 0
fi
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
git clone --filter=blob:none --no-checkout https://github.com/google-research/harness2 "$tmp/repo"
git -C "$tmp/repo" checkout "$commit"
mkdir -p "$dest"
cp -r "$tmp/repo/harness2" "$dest/"
cp "$tmp/repo/LICENSE" "$tmp/repo/README.md" "$tmp/repo/pyproject.toml" "$dest/"
printf 'Source: google-research/harness2\nCommit: %s\n' "$commit" > "$dest/PROVENANCE.md"
echo "Pinned upstream copied to $dest; run scripts/verify_vendor.sh after regenerating MANIFEST.sha256."
