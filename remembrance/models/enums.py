from enum import StrEnum

class MemoryType(StrEnum):
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    EPISODIC = "episodic"
    IDENTITY = "identity"
    TASK = "task"
    POLICY = "policy"

class MemoryTier(StrEnum):
    WORKING = "working"
    LONG_TERM = "long_term"

class GateDecision(StrEnum):
    REJECT = "reject"
    WORKING_ONLY = "working_only"
    PROMOTE_SEMANTIC = "promote_semantic"
    PROMOTE_PROCEDURAL = "promote_procedural"
    ARCHIVE_CONFLICT = "archive_conflict"

class ProposalType(StrEnum):
    ADD = "add"
    UPDATE = "update"
    MERGE = "merge"
    DEPRECATE = "deprecate"
    ROLLBACK = "rollback"

class ProposalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"
