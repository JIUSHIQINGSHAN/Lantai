"""反思 worker（spec: docs/plans/reflection-module-spec.md）

- run_reflect_once：健康扫描 → 蒸馏 → 提案化 → 自动应用/待审 → 健康快照自证
  核心逻辑在 lantai/evolution/reflector.py；record_run 由核心函数内部记录。
"""

from lantai.evolution.reflector import run_reflect_once as _run_reflect


def run_reflect_once() -> dict:
    res = _run_reflect(source="scheduled")
    try:
        from lantai.cognition.reflection import ReflectionEngine
        from lantai.storage import db

        with db.get_session() as s:
            engine = ReflectionEngine(s)
            report = engine.run_reflection()
            res["cognitive_reflection"] = {
                "new_patterns": report.new_patterns,
                "belief_candidates": report.belief_candidates,
                "rule_candidates": report.rule_candidates,
                "rules_weakened": report.rules_weakened,
                "failures": report.failures,
                "summary": report.summary,
            }
    except Exception:
        pass
    return res
