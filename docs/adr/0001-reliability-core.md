# ADR 0001: First-week reliability core boundaries

- **Status:** Accepted
- **Date:** 2026-07-15

## Context

`bmlsub` rebuilds its reliability core so reliability concepts are not mixed with the legacy
pipeline's path-based state assumptions. Existing media functions will be copied from `bmlsub`
only when a real migrated stage needs them.

## Decision

- Day 1 and Day 2 implement only the shared state language and SQLite run ledger.
- Default state lives at `<workspace>/.bmlsub/state.sqlite3`; callers may override the state directory.
- SQLite is the execution-state source for migrated stages. File existence alone is not success.
- Future CLI stdout is final machine-readable JSON; progress and diagnostics use stderr.
- `needs_review` is distinct from `failed` and represents a safe refusal to automate uncertain work.
- Future ASS-aware conversion defaults to `needs_review` when reliable targets cannot be identified.
  Full-file conversion must be explicitly requested.
- Input, parameter, tool, and artifact identity remain separate fingerprint concepts.
- Core models use only the Python standard library and do not depend on CLI, HTTP, or media tools.
- Metadata and diagnostics reject credential-like keys. SQLite must not store complete subtitle text
  or credentials.

## Non-goals

No GUI, daemon, remote worker, generic workflow DSL, external queue, full event sourcing, or media
stage migration is included in Day 1/2.

## Schema policy

Schema version 1 creates `runs`, `stages`, `artifacts`, and minimal lifecycle `events` in one
transaction. Initialization is idempotent. Unsupported versions are rejected rather than silently
changed.
