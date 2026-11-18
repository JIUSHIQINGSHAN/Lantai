# ADR-0088: 摄取归因与阶段监控

Status: proposed (implementation branch only)

## Context

`ingest_background_only` originally compared session-marked retrieval rows with newly created MemoryItem rows. It did not observe candidates, proposals, dedup/reinforce/update outcomes, or event-source context. Production read-only snapshots and isolated SQLite experiments showed that a pending/rejected candidate can be real ingestion while no MemoryItem is created, and another session or archived/future row can mask the gap.

The installed DSH MCP adapter also receives `ToolExecution` but its default call path sends only `{name, arguments}`. The deployed Python bridge consumes only `params.arguments`; top-level `_meta` is not forwarded. A global `X-Session-Id` is unsafe because existing archive/graph readers consume `Principal.session_id` as scope.

## Decision

1. Treat request attribution as bounded, optional, untrusted metadata. It never authenticates, selects a tenant, grants ACL, or changes read scope.
2. Keep `origin_session_id/turn` separate from `current_request_session_id`, explicit `scope_session_id`, trace/span IDs, request/ingest event IDs and idempotency keys.
3. Record a zero-body `IngestEvent` at the receiving boundary and attach candidate/proposal/final or affected existing memory outcomes transactionally where possible. Preserve historical NULLs and existing MemoryItem origin; merge/update adds a relation/outcome rather than overwriting origin.
4. Keep valid background jobs sessionless. Candidate pending/rejected is a received outcome, not an applied knowledge success and not a wiring failure.
5. Monitor the half-open UTC window `[start,end)`, with `end <= sample_now`, explicit state/source/owner scopes, bounded aggregates, and exact/partial session correlations. Missing stage tables are explicitly unavailable, never silently green. Legacy item counts remain for compatibility but NULL is labeled unknown, not background.
6. DSH attribution is delivered by an explicit per-call Host adapter envelope under `io.lantai/attribution`; the existing bridge must explicitly whitelist and map it. It is not delivered by global mutable context, a universal REST header, or model-authored arguments.
7. Tool discovery must complete before replacing registrations. Preserve SDK pagination/validation but reject repeated continuation cursors and retain the previous set on discovery failure. REST failures become MCP tool failures (`isError`), with status-only HTTP diagnostics; a cancelled client wait does not imply rollback of an accepted write. No response is emitted for protocol notifications.

## Compatibility and rollback

DSH human-turn recall and the standalone remote MCP client use independent integrations with separate opt-in controls (ADR-0089); neither changes attribution persistence or auto-enables dialogue ingestion.

All wire attribution fields are optional. Old clients retain their origin/NULL behavior; requests gain an additive ingest_event_id result. No history backfill is performed. New ingestion requires schema v46 and fail-loud event persistence; there is no runtime switch that silently bypasses its accounting. Legacy monitor readers can inspect candidate/proposal stages if the event table is missing, and explicitly report event-stage availability. For code rollback retain the additive event table; never drop it or rewrite old memory rows. The Host bundle can be disabled and the previous MCP entry re-enabled without changing stored data.

Completed event outcomes have configurable retention (default 30 days, minimum one day), based on updated_at. Unresolved processing/candidate/pending events are retained so later decisions can attach. This is a bounded-retention diagnostic association, not a permanent audit ledger or event-sourced history; each event holds its latest outcome. Memory origin remains unchanged when the association expires.

## Verification

The branch includes real SQLite stage/outcome tests, a 16-case isolation matrix under the modern dependency worktree, the installed SDK/stdio/localhost bridge contract tests, and owner/scope regressions. Production was queried only with a read-only SQLite transaction; no production write or secret read was performed.
