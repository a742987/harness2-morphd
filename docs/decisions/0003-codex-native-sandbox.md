# ADR-0003: Codex bypass flags are not a consumer default

**Status:** accepted

## Context

The internal engine reused upstream argv containing
`--dangerously-bypass-approvals-and-sandbox` and `--dangerously-bypass-hook-trust`. The PRD
forbids bypass as a consumer default.

## Decision

Codex runs with `--sandbox workspace-write --ask-for-approval never` and no hook-trust bypass.
Preflight requires those flags. If a Codex version cannot provide a usable non-bypass sandbox,
Codex is marked beta/unsupported rather than weakening security.

## Consequences

- Some old codex versions become unsupported.
- The consumer/engine does not expose a "bypass" shortcut.
- Container/egress remains opt-in strict mode; native sandbox is the baseline.
