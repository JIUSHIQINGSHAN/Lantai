# ADR 0040: API Key Authentication and Telemetry (F13)

## Status
Accepted

## Context
As Lantai scales to multiple tenants/agents, relying on the client to honestly pass `user_id` and `lane` via request payloads is insecure. We need a robust mechanism to authenticate callers, strictly isolate their data (ACL), and track API usage for rate limiting and telemetry.

## Decision
1. **Authentication (Bearer Token)**:
   - Clients must pass `Authorization: Bearer <API_KEY>`.
   - Keys will be validated against an `ApiKey` SQLite table.
   - For local development, if no keys exist in the database, a default `dev_key` will be auto-generated or the auth check will allow a fallback mode (configurable via settings).

2. **Data Isolation (ACL)**:
   - A `SecurityContext` (containing `user_id` and `allowed_lanes`) will be injected into every request via FastAPI dependencies.
   - All underlying queries (ORM, FTS, Vector) must be intercepted or explicitly filter by `user_id = ? AND lane IN (?)`.

3. **Telemetry & Rate Limiting**:
   - Introduce an `OperationLog` table to record request metrics (endpoint, latency, user_id).
   - Implement basic rate limiting based on the `ApiKey`'s `rate_limit_rpm` configuration using an in-memory bucket or DB log counting.

## Consequences
- Better security and strict multi-tenant boundaries.
- slight latency overhead (1 DB query for auth cache, though we can use Python `@lru_cache` for keys).
- All existing clients must be updated to pass the `Authorization` header.
