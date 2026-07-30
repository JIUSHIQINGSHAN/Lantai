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
