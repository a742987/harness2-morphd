# ADR-0001: Consumer analysis uses the engine primitives, not the strict sweep

**Status:** accepted

## Context

The strict Harness² path needs a task library, splits, 20–50 paired tasks and enterprise gates.
The consumer promise is one session → one explainable suggestion with a hard budget.

## Decision

The consumer analyzer builds a `task_contract: 1` evidence packet from the redacted session and
calls a BYOK Proposer directly, reusing engine redaction/security/store/budget primitives. The
strict sweep remains available via the CLI for enterprise mode. Consumer trials use online
session signals, not statistical gates.

## Consequences

- No mandatory task import for ordinary users; faster first value.
- Suggestion quality is bounded by one-session evidence; the product reports "no safe change"
  rather than fabricating confidence.
- Enterprise users can still build task libraries and run M1/M2 gates with the CLI.
