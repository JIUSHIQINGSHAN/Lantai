"""注入帧里每条记忆正文只出现一次（巡检 2026-11-16 22:00 改进，周报 2026-11-03 P2-4）。

病灶：build_context 把同一批命中写两遍——【本次依据】每行 `(id, score) 正文`，
【相关记忆】每行 `[score] 正文`。依据段还不受 SHELL_HOOK_MAX_TOTAL_CHARS 管，
实际注入最多近 2 倍。

修法：依据段只列 id + 分数（按顺序对应【相关记忆】条目），正文只留在【相关记忆】。
结构化的 out["evidence"]（插件按它取 used_ids 回执）不变。

测试纪律：真实内存库（param_env）+ 真 build_context，只 mock 外部向量库。
"""

import importlib.util
import os

import pytest

HOOK_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "shell_hook.py"
)

BODY_A = "兰台发版前先确认 master 的 CI 是绿的再打标签"
BODY_B = "票八十三的红测是用户特意留着的契约测试"


def _load_hook(name):
    spec = importlib.util.spec_from_file_location(name, HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed(sf, rows):
    from lantai.models.tables import MemoryItem

    with sf() as s:
        for r in rows:
            s.add(MemoryItem(**r))
        s.commit()


def _fake_store(ids):
    class _FakeStore:
        def search(self, qv, top_k=5, filters=None):
            return [{"id": i, "distance": 0.1 + 0.01 * n} for n, i in enumerate(ids)]

    return _FakeStore()


def _build(mod, ids, query="兰台发版和红测的记忆说了什么"):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("lantai.retrieval.hybrid.get_vector_store", lambda: _fake_store(ids))
        return mod.build_context(query)


def _sections(ctx):
    assert "【本次依据】" in ctx and "【相关记忆】" in ctx, ctx
    ev = ctx.split("【本次依据】", 1)[1].split("【相关记忆】", 1)[0]
    rel = ctx.split("【相关记忆】", 1)[1]
    return ev, rel


@pytest.fixture
def two_memories(param_env):
    sf, _engine = param_env
    _seed(
        sf,
        [
            {"id": "mem_dd_a", "memory_type": "semantic", "key": "dd_a", "content": BODY_A},
            {"id": "mem_dd_b", "memory_type": "semantic", "key": "dd_b", "content": BODY_B},
        ],
    )
    return sf


def test_each_body_appears_once_in_context(two_memories):
    """回归：两条命中，每条正文在整个注入帧里只出现一次（改前各 2 次）。"""
    out = _build(_load_hook("shell_hook_dd1"), ["mem_dd_a", "mem_dd_b"])
    ctx = out.get("context", "")
    assert ctx.count(BODY_A) == 1, ctx
    assert ctx.count(BODY_B) == 1, ctx


def test_evidence_section_lists_ids_and_scores_only(two_memories):
    """依据段只有 id + 分数，不含正文；正文只在【相关记忆】。"""
    out = _build(_load_hook("shell_hook_dd2"), ["mem_dd_a", "mem_dd_b"])
    ev, rel = _sections(out["context"])
    assert "mem_dd_a" in ev and "mem_dd_b" in ev
    assert BODY_A not in ev and BODY_B not in ev
    assert BODY_A in rel and BODY_B in rel
    for e in out["evidence"]:
        assert f"- ({e['id']}, score {e['score']})" in ev


def test_evidence_order_matches_related_memory_order(two_memories):
    """依据段的 id 顺序与【相关记忆】里正文的顺序一致（标题里承诺的「按顺序对应」）。"""
    out = _build(_load_hook("shell_hook_dd3"), ["mem_dd_a", "mem_dd_b"])
    ev, rel = _sections(out["context"])
    by_id = {"mem_dd_a": BODY_A, "mem_dd_b": BODY_B}
    ev_order = sorted(by_id, key=ev.index)
    rel_order = sorted(by_id, key=lambda i: rel.index(by_id[i]))
    assert ev_order == rel_order
    assert [e["id"] for e in out["evidence"]] == ev_order


def test_structured_evidence_unchanged_for_receipts(two_memories):
    """回执/审计走的结构化 evidence 不变：仍带 id、content、score，content 是正文。"""
    out = _build(_load_hook("shell_hook_dd4"), ["mem_dd_a", "mem_dd_b"])
    ev = {e["id"]: e for e in out["evidence"]}
    assert set(ev) == {"mem_dd_a", "mem_dd_b"}
    assert ev["mem_dd_a"]["content"] == BODY_A
    assert ev["mem_dd_b"]["content"] == BODY_B
    assert all(isinstance(e["score"], float) for e in ev.values())
    assert out.get("event_id")  # 回执链仍带 event_id


def test_plugin_used_ids_derivation_unchanged(two_memories):
    """插件从 evidence 取 used_ids（hermes-plugin/lantai-hook/__init__.py 同款推导），结果不变。"""
    out = _build(_load_hook("shell_hook_dd5"), ["mem_dd_a", "mem_dd_b"])
    used_ids = [
        e.get("id") for e in (out.get("evidence") or []) if isinstance(e, dict) and e.get("id")
    ]
    assert sorted(used_ids) == ["mem_dd_a", "mem_dd_b"]


def test_long_body_saves_roughly_one_copy(param_env):
    """长正文（接近单条上限）：注入帧比「正文写两遍」至少少一份截断后正文的长度。"""
    from lantai.core.settings import settings

    sf, _engine = param_env
    per = settings.SHELL_HOOK_MAX_CHARS_PER_MEMORY
    body = ("兰台发版流程的细节" * 200)[: max(10, per - 5)]
    _seed(sf, [{"id": "mem_dd_long", "memory_type": "semantic", "key": "dd_l", "content": body}])
    out = _build(_load_hook("shell_hook_dd6"), ["mem_dd_long"])
    ctx = out["context"]
    content = out["evidence"][0]["content"]
    assert ctx.count(content) == 1
    ev, _rel = _sections(ctx)
    assert len(ev) < 200  # 只剩标题尾巴 + 围栏 + 一行 id/分数
