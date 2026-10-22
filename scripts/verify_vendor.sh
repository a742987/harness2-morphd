#!/bin/sh
set -eu
cd "$(dirname "$0")/../vendor/harness2-src"
sha256sum -c MANIFEST.sha256
