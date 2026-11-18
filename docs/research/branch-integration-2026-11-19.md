# Branch integration record — 2026-11-19

The maintainer requested integration of all local and remote branches into `master`. Source commits are retained as merge ancestors, including equivalent changes previously cherry-picked. Branches and worktrees are not evidence of deployment; the release gate still determines publication readiness.

| Source | Resolution |
| --- | --- |
| `fix/candidate-scheduler-20261117` (`2c6466d7`) | Already a mainline ancestor; preserve candidate review, indexing and scheduler liveness fixes. |
| `upgrade/modern-stack-20261117` (`f9999a23`) | Already a mainline ancestor; preserve Python/SQLModel/Chroma upgrade and migration safeguards. |
| `fix/session-attribution-20261118`, `integration/session-attribution-20261118` (`5da3106f`) | Already mainline ancestors; preserve optional attribution and staged ingestion monitoring. |
| `fix/v0.68-closeout-contracts` (`cf4804f0`) | Already a mainline ancestor. |
| `fix/web-guidance-ui-followup`, `pr56-autopilot-ready` (`ff0166b9`) | Base commits already included; relevant UI changes and browser tests were integrated in `3e9e0e41`. Local runtime output remains local. |
| Original Hermes fix (`572e0968`) and PR #56 head (`44d89526`) | Original history merged, with equivalent code already included. Keep the later anonymized research/test fixture and portable installation paths. |
| DSH recall draft (`80042aee`) | Merged original history. Every recall source, locale, patch and test blob is identical in `integrations/dsh-lantai-recall/`; CLI source/tests remain identical at their original paths. Keep the newer attribution bundle at `integrations/dsh-lantai/`. Manifest formatting differs only. |
| `origin/proposal/daily-2026-11-16` (`655b1974`), `.../2026-11-17` (`b7565cc2`), `.../2026-11-18` (`8fde0382`) | Research documents and original histories merged; these are proposals, not claims that their suggested features were implemented. |
| `origin/arena/01a07697-lantai` (`84f86930`) | Its monitor backend, routes, JS, metrics, telemetry, tests and ops runbook are byte-identical to their counterparts in accepted mainline commit `315f3b87`. Keep later security, retention and scheduler fixes. The old ADR-0044 monitor text differs only in its title from current ADR-0045; do not add a second ADR-0044 beside the existing cognition ADR. |
| `origin/arena/01a07693-lantai` (`03ccfeff`) | Alternative monitor design merged against the maintained ADR-0045 interface. Existing domain counts, Chroma collection compatibility and restart-safe scheduler behavior cover its fixes. Keep authenticated `/monitor/overview`, shared worker policy and current front end rather than reintroducing the obsolete global `/monitor/snapshot` contract. Port its useful aggregate/queue/worker/HTTP tests to current contracts in `tests/test_monitor_panel.py`. |
| `merge-master/master` (`b044b637`) | Already a mainline ancestor. |

## Conflict decisions

- Keep current API authentication and Principal propagation, scoped counts and redacted runtime fields. Do not revive the arena's unscoped routes, hardcoded version strings, telemetry retention gaps or persistent scheduler-store behavior.
- Retain both DSH bundles with different package names and registration IDs. Both remain disabled/opt-in; source integration does not enable either in a user's profile.
- Preserve current packaging dependencies and release version. Old arena manifests describe v0.21.0 and are not a dependency downgrade request.
- Retain the original alternative source through merge ancestry; no force push, history rewrite or remote branch deletion is required.

## Verification

Four ported arena tests use real SQLite and actual monitor aggregation/routes; no internal monitor calculation is mocked. Existing monitor, ownership, scheduler, UI and vector-store tests remain the primary maintained coverage. Final suite and release gate results are recorded separately after the complete merged tree is tested.
