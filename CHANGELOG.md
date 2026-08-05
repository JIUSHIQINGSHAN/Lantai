# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Retrieval noise filtering**: `RetrievalEvent.is_system_noise` field + `is_system_noise()` classifier (deterministic prefixes + length gap), `scripts/mark_retrieval_noise.py` for idempotent backfill of legacy events
- **Hermes desktop injection plugin**: `remembrance-hook` Python plugin registering `pre_llm_call` (serve mode runs no shell hooks — `_AGENT_COMMANDS` excludes `serve`); resident `shell_hook.py --serve` NDJSON loop eliminates cold-start cost
- **Hermes onboarding scripts**: `scripts/migrate_home.py` (safe REMEMBRANCE_HOME migration), `scripts/verify_remembrance.py` (8-point self-check), `docs/hermes-install-handoff.md`
- **Manual call guide**: `docs/remembrance-manual-call.md` — Hermes chat / CLI JSON-RPC / REST API entry points

### Fixed
- **UTF-8 stdin corruption**: force `sys.stdin/stdout.reconfigure(encoding="utf-8")` in `mcp_server.py` and `shell_hook.py` — Windows GBK decoding turned Chinese queries into mojibake (「你好」→「浣犲ソ」) causing zero-recall + `no_signal`
- **Hermes shell-hook interpreter**: hooks config now points to `.venv-audit` python (hermes venv lacked sqlmodel); serve mode uses plugin channel instead
- **shell_hook timeout semantics**: single-shot mode returns `{}` on timeout instead of `os._exit` (serve mode needs resilience)

## [0.3.7] - 2026-08-04

### Fixed
- **Data loss fix**: `apply_proposal` now accepts `APPROVED` status — human approval and `run_pending` paths were previously broken (found in live deployment)
- **SQLite self-deadlock**: Use outer session for `MemoryEdge` in `apply_proposal` — nested session caused deadlocks under concurrent writes (found in live deployment)
- **Gate threshold isolation**: Pin `GATE_MIN` in test to isolate from host `.env` pollution

### Changed
- Untrack `.workbuddy` session metadata (keep on disk), keep parallel-session prompt doc in `docs/`

### Removed
- Root-level empty `remembrance__init__.py` (0-byte junk re-added in previous commit)
- P2 plan (tidal-coalescing + MCP) — superseded by v0.3.1/v0.3.3 implementations
- Accidentally removed `docs/plans/` restored

## [0.3.6] - 2026-07-31

### Added
- Comprehensive README with architecture diagram, features table, quickstart, API reference, and testing guide
- README rewritten in aiduMEM style (with adaptation credit)
- MIT LICENSE

### Fixed
- Removed empty `remembrance__init__.py` from root

## [0.3.5] - 2026-07-28

### Added
- Test suite: 120 tests, all green
  - FTS5 integration tests
  - SSRF safety tests
  - Backup/recovery tests
  - MCP protocol tests
  - Shell Hook timeout tests

### Security
- Supply chain hardening: GitHub Actions pinned to commit SHA (not mutable tags)
- Docker images run as non-root

## [0.3.4] - 2026-07-25

### Added
- FTS5 trigram parallel recall + BM25 caching ([ADR-0008](docs/adr/0008-fts5-parallel-recall.md))

## [0.3.3] - 2026-07-22

### Added
- SSRF hardening: external fetch protocol whitelist + DNS resolution IP blocking
- Atomic backup/recovery with online backup + manifest SHA256 validation
- MCP server: input validation + exception isolation

## [0.3.2] - 2026-07-18

### Added
- FTS5 schema + Chronos timezone + BM25 compatibility fixes

## [0.3.1] - 2026-07-15

### Added
- P0 audit remediation:
  - Repository hygiene
  - Binding authentication enforcement
  - Test baseline establishment

## [0.1.0] - 2026-06-20

### Added
- Initial release adapted from [aiduMEM](https://github.com/monkey2jack/aiduMIT)
- Storage layer: SQLite + FTS5 + ChromaDB
- Four-path hybrid retrieval: vector + BM25 + FTS5 trigram + decay
- Relevance gate, Tidal coalescing, Fastpath, Dedup, Ebbinghaus forgetting, Chronos
- Shell Hook + MCP dual-mode integration
- Security baseline: loopback binding, SSRF guard, atomic backup, endpoint whitelist
