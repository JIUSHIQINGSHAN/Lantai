# ADR 0041: Standalone Worker Deployment (F8)

## Status
Accepted

## Context
Currently, `apscheduler` runs as a background thread inside `api_server.py`. This tightly couples the API layer with the background processing layer (ingestion, reflection, consolidation). It prevents us from horizontally scaling the API servers, because multiple API servers would launch multiple schedulers, causing duplicate job executions.

## Decision
1. **Decouple Scheduler**: 
   - Add a `LANTAI_RUN_SCHEDULER` flag to toggle the scheduler on/off in the API server.
2. **Database Job Store**:
   - Reconfigure `apscheduler` to use `SQLAlchemyJobStore` connected to the same SQLite database (`apscheduler_jobs` table).
   - This ensures that if multiple worker nodes start, the DB acts as a distributed lock/coordinator for job executions.
3. **Standalone Entrypoint**:
   - Provide `scripts/run_worker.py` to allow deploying workers in isolated containers or processes.

## Consequences
- Allows independent scaling of API and Worker tiers.
- Prevents duplicate cron job triggers in multi-instance deployments.
- Requires a new DB table `apscheduler_jobs`.
