# ADR 0042: Cascade Document Delete (F10)

## Context
Documents imported into Lantai (e.g. via `POST /import/jsonl` or `POST /documents`) currently generate `MemoryItem` records. However, if a user wants to un-ingest a document, there is no straightforward way to delete the document and synchronously clean up all derived memory chunks, their FTS indices, and any related semantic graph edges.

## Decision
We will implement a `DELETE /documents/{document_id}` endpoint.
- It will find all `MemoryItem` instances where `document_id` matches the input.
- It will soft-delete (update `status` to `deleted` or `archived`) or hard-delete those `MemoryItem`s depending on configuration. For now, we will perform a **hard delete** to reclaim SQLite space and remove them from FTS.
- It will issue a cleanup to `memory_fts` for all deleted `MemoryItem` IDs.
- It will delete the original `Document` record if it exists in a new `Document` table (or just delete the MemoryItems if `document_id` is just a logical field).
- (Note: currently, we have `source` and `document_id` in `MemoryItem` schema. We will query by `document_id`.)

## Consequences
- Requires a transaction that covers `MemoryItem` deletion, FTS cleanup, and `Edge` deletion.
- Ensures the user can safely prune bad imports.
- Reclaims database space.
