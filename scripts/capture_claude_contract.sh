#!/bin/sh
# Capture a tiny, sanitised Claude Code contract sample. Run inside the container.
# Usage: scripts/capture_claude_contract.sh <workspace> <output-dir>
set -eu
ws=${1:?workspace}
out=${2:?output directory}
mkdir -p "$out"
cd "$ws"
claude -p \
  --output-format stream-json \
  --verbose \
  --permission-mode acceptEdits \
  --allowedTools "Read,Edit,Write,Glob,Grep,Bash" \
  --disallowedTools "WebFetch,WebSearch" \
  --setting-sources project \
  --max-budget-usd 0.05 \
  --session-id "$(python3 -c 'import uuid;print(uuid.uuid4())')" \
  "Create a file named morphd-contract.txt containing the word hello. Then stop." \
  > "$out/stream-json.jsonl" 2> "$out/stderr.txt"
printf 'Captured %s; review and redact before committing.\n' "$out/stream-json.jsonl"
