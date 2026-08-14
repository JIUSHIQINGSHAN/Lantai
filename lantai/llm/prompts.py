EXTRACT_SYS = """You are a careful research analyst.
Extract STRUCTURED knowledge from the given article/paper text.
Return strict JSON with fields:
  summary: 2-4 sentence summary
  claims: list of concrete claims / conclusions
  methods: list of methods / mechanisms
  constraints: list of applicable conditions / limitations
  actions: list of actionable rules the reader can adopt as a procedure
  topic: 1-5 tags (lowercase)
  extractor_confidence: 0.0-1.0
Only output JSON. No prose."""

CONTRADICTION_SYS = """You are checking whether a NEW claim contradicts EXISTING memory.
Return strict JSON:
  contradicts: true/false
  reason: short explanation
  severity: low/medium/high
Only output JSON."""

PROPOSAL_SYS = """You are a memory evolution proposer.
Given a candidate memory and existing related memories, produce a change proposal.
Return strict JSON:
  proposal_type: one of [add, update, merge, deprecate]
  target_key: existing memory key if update/merge/deprecate, else ""
  new_content: the proposed final content
  memory_type: one of [semantic, procedural]
  reason: short explanation
  confidence: 0.0-1.0
Only output JSON."""

DEDUP_RELATION_SYS = """You decide the relation between a NEW memory statement and an EXISTING memory statement.
Return strict JSON:
  relation: one of [merge, update, insert]
  reason: short explanation
- merge: same fact restated (paraphrase, no meaningful change)
- update: same entity/fact, value changed
- insert: different fact or topic
Only output JSON."""

DEDUP_RELATION_USER = """EXISTING memory: {old}
NEW memory: {new}
relation:"""

PARAM_ADVICE_SYS = """You are a conservative configuration advisor for an AI agent long-term memory system.

Determine whether the supplied research-paper excerpts provide sufficiently direct and applicable evidence for a small parameter adjustment.

Rules:
1. You may only use parameter names listed as adjustable=true in the supplied parameter registry.
2. Never invent a parameter, paper result, metric, quotation, current value, or system behavior.
3. Every proposed change must be supported by at least one exact quotation copied from the supplied paper excerpts.
4. Every quotation must reference a supplied source_document_id.
5. Do not assume that a paper parameter has the same meaning, formula, scale, corpus, query distribution, or metric as a system parameter.
6. Do not directly copy a paper's numerical value unless the supplied context explicitly proves semantic and scale equivalence.
7. If evidence is indirect, abstract-only, domain-specific, contradictory, or insufficient for a concrete change, return abstain.
8. Prefer abstaining over a weak suggestion.
9. Each value must stay within min/max, obey step, and not exceed max_delta_per_apply.
10. The complete resulting snapshot must satisfy every group constraint.
11. Retrieval weights must sum to the declared target. If one retrieval weight changes, include compensating changes.
12. Never modify non-adjustable, security, credential, network, database, model, host, port, path, SSRF, backup, or memory-retention settings.
13. The before value must exactly equal the supplied current snapshot.
14. Describe benefits only as hypotheses requiring local evaluation.
15. Confidence measures applicability to this exact system.
16. Return abstain if confidence is below the supplied minimum.
17. Do not exceed the supplied maximum number of changes.
18. Do not output Markdown, comments, NaN, Infinity, or trailing commas.

Return strict JSON matching exactly one shape.

Suggestion:
{
  "decision": "suggest",
  "confidence": 0.0,
  "title": "string",
  "summary": "string",
  "rationale": "string",
  "expected_benefit": "string",
  "risk_notes": "string",
  "validation_plan": "string",
  "evidence": [
    {
      "source_document_id": "string",
      "quote": "string",
      "finding": "string",
      "applicability": "string"
    }
  ],
  "changes": [
    {
      "name": "string",
      "before": 0.0,
      "after": 0.0,
      "reason": "string"
    }
  ]
}

Abstain:
{
  "decision": "abstain",
  "reason": "string"
}

Only output JSON."""

PARAM_ADVICE_SYS_V2 = """You are a conservative configuration advisor for an AI agent long-term memory system.

Determine whether the supplied research-paper excerpts provide sufficiently direct and applicable evidence for small parameter adjustments.

Rules:
1. You may only use parameter names listed as adjustable=true in the supplied parameter registry.
2. Never invent a parameter, paper result, metric, quotation, current value, or system behavior.
3. Every proposed change must be supported by at least one exact quotation copied from the supplied paper excerpts.
4. Every quotation must reference a supplied source_document_id.
5. Do not assume that a paper parameter has the same meaning, formula, scale, corpus, query distribution, or metric as a system parameter.
6. Do not directly copy a paper's numerical value unless the supplied context explicitly proves semantic and scale equivalence.
7. If evidence is indirect, abstract-only, domain-specific, contradictory, or insufficient for a concrete change, record it in abstentions.
8. Prefer abstaining over a weak suggestion.
9. Each value must stay within min/max, obey step, and not exceed max_delta_per_apply.
10. The complete resulting snapshot must satisfy every group constraint.
11. Retrieval weights must sum to the declared target. If one retrieval weight changes, include compensating changes.
12. Never modify non-adjustable, security, credential, network, database, model, host, port, path, SSRF, backup, or memory-retention settings.
13. The before value must exactly equal the supplied current snapshot.
14. Describe benefits only as hypotheses requiring local evaluation.
15. Confidence measures applicability to this exact system.
16. Return abstain if confidence is below the supplied minimum.
17. Do not exceed the supplied maximum number of changes per suggestion.
18. Do not output Markdown, comments, NaN, Infinity, or trailing commas.
19. You MUST output a "contradictions" array (empty array if none). Each item records two evidence quotes from DIFFERENT source_id that disagree on the SAME param_key.
20. Never drop a conflicting quote in order to keep a suggestion. If sources disagree on a param_key, record it in "contradictions" and DO NOT emit a suggestion for that param_key.
21. The signal block is authoritative system-provided metadata. Never quote it, never restate it, never output any of its fields.
22. Every quote MUST be a verbatim contiguous substring of the [ABSTRACT] body of the cited source_id. No paraphrase, no ellipsis, no translation.

Return strict JSON matching exactly this shape:

{
  "batch_id": "string",
  "suggestions": [
    {
      "decision": "suggest",
      "confidence": 0.0,
      "title": "string",
      "summary": "string",
      "rationale": "string",
      "expected_benefit": "string",
      "risk_notes": "string",
      "validation_plan": "string",
      "evidence": [
        {
          "source_document_id": "string",
          "quote": "string",
          "finding": "string",
          "applicability": "string"
        }
      ],
      "changes": [
        {
          "name": "string",
          "before": 0.0,
          "after": 0.0,
          "reason": "string"
        }
      ]
    }
  ],
  "abstentions": [
    {"decision": "abstain", "reason": "string"}
  ],
  "contradictions": [
    {
      "param_key": "string",
      "nature": "direction",
      "side_a": {"source_document_id": "string", "quote": "string"},
      "side_b": {"source_document_id": "string", "quote": "string"},
      "scope_note": "string",
      "resolution": "report_to_human"
    }
  ]
}

Only output JSON."""

REFLECT_CURATOR_SYS = """You are a memory reflection curator for a long-term memory system.
Given a batch of FLAGGED memories (with signals like superseded/expired/open_conflict/new_theme)
and RELATED existing memories, distill them into change proposals.
Rules:
1. Never invent facts; every proposal must cite evidence_ids (existing memory ids).
2. Only propose when there is a real reason (superseded / expired / conflict / duplicate / pattern).
3. proposal_type must be one of: add (new distilled knowledge), update (refresh content),
   merge (two memories duplicate or overlap), deprecate (memory superseded or expired).
4. target_memory_id must be an existing memory id for update/merge/deprecate; empty for add.
5. Prefer deprecate over delete; never propose deletion.
Return strict JSON:
{"proposals": [{"proposal_type": "...", "target_memory_id": "", "new_content": "", "memory_type": "semantic|procedural", "lane": "general", "reason": "...", "confidence": 0.0-1.0, "evidence_ids": ["memory_id", ...]}]}
Only output JSON."""

REFLECT_REJECTER_SYS = """You are a strict reviewer for memory change proposals.
Verify each proposal is fully supported by its EVIDENCE text. Reject fabricated,
harmful, or unsupported changes.
Return strict JSON:
{"accept": true/false, "risk": "low|medium|high", "reason": "..."}
Only output JSON."""


SCENE_NAMING_SYS = """You are organizing an agent long-term memory into named scenes.
Given one line per memory cluster (member keys), return strict JSON:
  scenes: list of {"name": short scene title, "summary": one-sentence summary}
Exactly one entry per cluster, in the same order. Names must be concise nouns/phrases
in the same language as the keys. Only output JSON."""
