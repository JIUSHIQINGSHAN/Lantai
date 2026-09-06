# ADR 0043: Dynamic Prompt Management (F14)

## Context
System prompts (such as `EXTRACT_SYS`, `CONTRADICTION_SYS`, `PROPOSAL_SYS`, etc.) are currently hardcoded as constants in `lantai/llm/prompts.py`. If a user needs to tweak these prompts for better extraction accuracy or different behaviors, they must modify the source code and restart the system.

## Decision
We will introduce a `PromptTemplate` table in the database and corresponding API endpoints (`GET /prompts/{id}`, `PUT /prompts/{id}`).
- The system will fetch prompts via a `get_prompt(prompt_id, default)` function.
- If a prompt has been customized and saved in the DB, it will be used instead of the hardcoded constant.
- The hardcoded constants will serve as the default fallbacks.
- This allows on-the-fly prompt tuning without downtime.

## Consequences
- Requires querying the database for prompts before LLM calls. Given SQLite is local and fast, the overhead is minimal, but we may introduce an in-memory TTL cache if performance becomes an issue.
- Prompts can be safely tuned via the UI or API.
