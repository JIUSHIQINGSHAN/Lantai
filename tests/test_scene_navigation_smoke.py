"""scene 导航真冒烟（v0.68 测试诚实性审计票 02 误报改正补测）。

审计曾把 `build_scene_navigation_lines` 列为死代码：triage 的 prod_calls
grep 只扫 `lantai/` + `api_server.py`，漏掉 `scripts/`。实际
`scripts/shell_hook.py:433 → _build_scene_lines → 本函数` 自 v0.6
（2b41b47b）起就是宿主 hook 检索响应的活链路，但测试面零覆盖。
本文件真实构造 MemoryScene 行 + 命中对象直调，验证 heat 降序组装与
零侵入降级，不 mock 内部逻辑（库走 param_env 真库）。
"""

from types import SimpleNamespace


def test_build_scene_navigation_lines_orders_by_heat(param_env):
    from lantai.models.tables import MemoryScene
    from lantai.services.scene_service import build_scene_navigation_lines

    session_factory, _ = param_env
    with session_factory() as s:
        s.add(MemoryScene(id="sc-hot", name="热场景", summary="热度九", heat=9, member_count=2))
        s.add(MemoryScene(id="sc-cold", name="冷场景", summary="热度二", heat=2, member_count=1))
        s.commit()

    items = [
        SimpleNamespace(scene_id="sc-cold", key="mem-cold-1"),
        SimpleNamespace(scene_id="sc-hot", key="mem-hot-1"),
        SimpleNamespace(scene_id="sc-hot", key="mem-hot-2"),
        SimpleNamespace(scene_id=None, key="mem-noscene"),
    ]
    lines = build_scene_navigation_lines(items, per_scene_chars=200, suffix="…")

    assert len(lines) == 2, f"应按两个场景各出一条导航块，实得 {len(lines)}"
    assert "热场景" in lines[0] and "mem-hot-1" in lines[0]
    assert "冷场景" in lines[1] and "mem-cold-1" in lines[1]


def test_build_scene_navigation_lines_degrades_to_empty(param_env):
    from lantai.services.scene_service import build_scene_navigation_lines

    # 无 scene_id 与悬空 scene_id（库中无行）都零侵入降级为空列表
    no_scene = build_scene_navigation_lines([SimpleNamespace(scene_id=None, key="a")], 200, "…")
    ghost_scene = build_scene_navigation_lines(
        [SimpleNamespace(scene_id="sc-ghost", key="b")], 200, "…"
    )
    assert no_scene == [] and ghost_scene == []
