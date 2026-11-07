"""向量条目生命周期包装函数真冒烟（v0.68 测试诚实性审计补强）。

审计结论：`hybrid.delete_memory_item` / `hybrid.has_memory_item` 此前是
mocked-only——唯一引用它们的测试把**函数本身**整个 patch 掉来模拟向量库
故障（SQL 权威语义回归用例，合法但替不了直调冒烟）。本文件直调包装函数，
向量库用测试本地记录型替身（外部依赖口径），验证包装层的派发契约。
"""

import pytest


class _RecordingVectorStore:
    def __init__(self, present_ids=()):
        self.present_ids = set(present_ids)
        self.deleted = []

    def delete(self, ids):
        self.deleted.extend(ids)

    def has(self, memory_id):
        return memory_id in self.present_ids


@pytest.fixture()
def vs(monkeypatch):
    # 不依赖 param_env：delete/has 两个包装函数只碰向量库、不碰 DB。
    # 叠在 param_env 的 get_vector_store 补丁上会形成双层撤销栈（conftest
    # 「规范绑定复位」记载过的病根），单层 patch 才能干净还原。
    store = _RecordingVectorStore(present_ids={"mem-present"})
    import lantai.retrieval.hybrid as hybrid

    monkeypatch.setattr(hybrid, "get_vector_store", lambda: store)
    return store


def test_delete_memory_item_forwards_id(vs):
    from lantai.retrieval.hybrid import delete_memory_item

    delete_memory_item("mem-gone")
    assert vs.deleted == ["mem-gone"]


def test_has_memory_item_truthiness(vs):
    from lantai.retrieval.hybrid import has_memory_item

    assert has_memory_item("mem-present") is True
    assert has_memory_item("mem-absent") is False
