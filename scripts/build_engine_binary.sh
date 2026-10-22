#!/usr/bin/env bash
# Build one onedir engine for the current OS/arch. Signing is done by CI.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
python_bin="${PYTHON:-.venv/bin/python}"
sep=":"
if [[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* ]]; then sep=";"; fi
"$python_bin" -m PyInstaller \
  --noconfirm \
  --clean \
  --onefile \
  --name morphd-engine \
  --add-data "morphd/consumer/webui${sep}morphd/consumer/webui" \
  --add-data "morphd/schemas${sep}morphd/schemas" \
  --add-data "morphd/base_harnesses${sep}morphd/base_harnesses" \
  --add-data "morphd/templates${sep}morphd/templates" \
  --add-data "morphd/pricing.yaml${sep}morphd" \
  --add-data "vendor/harness2-src/harness2${sep}harness2" \
  --add-data "vendor/harness2-src/LICENSE${sep}harness2" \
  --collect-submodules keyring \
  --collect-submodules pystray \
  --collect-submodules PIL \
  --collect-data jsonschema \
  --hidden-import zstandard \
  --hidden-import cryptography \
  scripts/engine_entry.py
echo "engine built at dist/morphd-engine (onefile); copy it into packages/platform/<os>-<arch>/bin/"
