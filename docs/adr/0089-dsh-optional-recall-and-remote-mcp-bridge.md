# ADR-0089: DSH opt-in recall 与独立远程 MCP bridge

Date: 2026-11-19
Status: Accepted

## Context

DSH integration has separate jobs: per-call attribution for tools already exposed by Lantai, optional pre-step retrieval for a human turn, and a standalone remote MCP bridge for DSH installations that cannot launch the local server. These must not implicitly become automatic dialogue ingestion or share a session-wide mutable identity.

## Decision

Update (2026-11-19): the recall activation clause in item 2 is suspended by [ADR-0090](0090-mainline-workspace-safety-contracts.md). The separate MCP attribution adapter and explicit bridge remain supported; the recall research bundle rejects activation before side effects.

1. Keep the existing `lantai-attribution` tool adapter separate. It forwards bounded per-call attribution; the label is not authentication or an ACL.
2. Ship `lantai-recall` as a separate disabled-by-default Host bundle. It only runs after admission of a human main-session turn, requires an exact workspace allowlist, bounds text/output/time, blocks common credential-like material, and leaves normal session admission untouched. It does not register MCP tools, write dialogue, or checkpoint data.
3. Document that selected user text is sent to the configured Lantai service and returned excerpts become persisted conversation content. Disabling or revoking later does not erase those transcripts. Test with synthetic data and a dedicated service credential.
4. Ship `lantai-mcp-bridge` as a standalone stdio client. It never imports local Settings or opens the local database; it requires exactly one explicit API credential, defaults to HTTPS, disables redirects/proxy discovery, limits request/response size and timeout, and redacts upstream bodies.
5. Permit plaintext HTTP only with a separate opt-in and a literal loopback IP. Do not accept arbitrary hostnames or use transport session IDs as business identity.

## Compatibility and rollback

Both integrations are opt-in and additive. Existing local MCP and `include:mcp-lantai` setups continue unchanged. Disable the recall bundle or remove the bridge configuration to roll back. Attribution persistence is independent; do not delete schema-v46 records as an integration rollback.

## Verification

Recall: four Node loopback Host tests. Remote bridge: five subprocess stdio/HTTP tests using synthetic credentials and a loopback peer. These verify isolated adapter behavior; they do not authorize a production workspace, validate real credentials, or count as a human DSH GUI acceptance.
