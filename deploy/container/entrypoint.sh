#!/bin/sh
set -eu

: "${MORPHD_HOME:=/var/lib/morphd}"
: "${MORPHD_TASKS:=/data/tasks}"
export MORPHD_HOME MORPHD_TASKS
export MORPHD_CONTAINER_ISOLATED=1

cmd="${1:-run}"
case "$cmd" in
  -*) cmd=run ;;
esac
case "$cmd" in
  run|evaluate)
    if [ "${MORPHD_EGRESS_ENFORCED:-}" != "1" ]; then
      echo "morphd-entrypoint: MORPHD_EGRESS_ENFORCED=1 is required for real runs" >&2
      echo "The container runtime, not this script, must enforce the egress allowlist." >&2
      exit 1
    fi
    if [ -z "${MORPHD_EGRESS_ALLOWLIST:-}" ]; then
      echo "morphd-entrypoint: MORPHD_EGRESS_ALLOWLIST is required" >&2
      exit 1
    fi
    ;;
esac

exec morphd "$@"
