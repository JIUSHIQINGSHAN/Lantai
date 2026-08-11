"""反思 worker（spec: docs/plans/reflection-module-spec.md）

- run_reflect_once：健康扫描 → 蒸馏 → 提案化 → 自动应用/待审 → 健康快照自证
  核心逻辑在 lantai/evolution/reflector.py；record_run 由核心函数内部记录。
"""
from lantai.evolution.reflector import run_reflect_once as _run_reflect


def run_reflect_once() -> dict:
    return _run_reflect()
