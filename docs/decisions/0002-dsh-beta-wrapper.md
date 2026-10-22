# ADR-0002: dsh uses a wrapper with a conservative idle fallback (beta)

**Status:** accepted (revisit after real plugin evidence)

## Context

The PRD prefers a native dsh Cordis plugin and a session-close event. The pinned dsh plugin API
and close event are not evidenced in this repository; guessing an API could corrupt a profile.

## Decision

Installation writes a wrapper and a conservative dsh session watcher. A session is considered
ended only when the log's last event is `turn/end` and the file has been unchanged for
`MORPHD_SESSION_IDLE_S` (default 60s), or when the host exits. The native plugin remains a
beta path to validate on a real dsh installation; we do not fake native support.

## Consequences

- Auto-trigger may be delayed; analysis can resume on the next host start.
- Profile configuration is not rewritten without a verified plugin contract.
- C.6 DoD remains open until real dsh evidence exists.
