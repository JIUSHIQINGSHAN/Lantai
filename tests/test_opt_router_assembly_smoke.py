"""装配表锁：同一路由不得注册两遍。

`CORE_ROUTERS` 里 `routes_diben_router` 出现过两次，同一路由（连同它的依赖
与中间件）挂了两份——OpenAPI 生成时刷三条 Duplicate Operation ID，路径也
重复进装配循环。

本锁不数列表长度（那只防住「又加一行」），而是**从 OpenAPI 反查**：同一
(path, method) 只应出现一次，且不产生 Duplicate Operation ID 警告。这样
「换个写法再重复挂一次」也会被咬住。
"""

import warnings


def _app():
    from lantai.api.app import app

    return app


def test_no_duplicate_operation_ids():
    """装配后不应出现 Duplicate Operation ID（修前三条）。"""
    app = _app()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        app.openapi()
    dups = [str(w.message) for w in caught if "Duplicate" in str(w.message)]
    assert not dups, f"重复注册：{dups}"


def test_diben_routes_present_once():
    """diben 三条路径仍在（删重复不等于删路由），且各方法只注册一次。"""
    spec = _app().openapi()
    # 走 OpenAPI（路由对象在新版 FastAPI 里是 `_IncludedRouter` 包装，
    # 没有 `path` 属性；OpenAPI 是装配结果的确定性视图）
    diben = {p: sorted(m.upper() for m in ops) for p, ops in spec["paths"].items() if "diben" in p}
    assert diben, "diben 路由一条都没挂（删多了）"
    assert set(diben) == {"/diben/anchor", "/diben/drift", "/diben/latest"}, diben
    for path, methods in diben.items():
        assert len(methods) == len(set(methods)), f"{path} 的方法重复：{methods}"


def test_core_router_list_has_no_duplicate_entries(monkeypatch):
    """列表本身也不得含同一对象两次（这条直接咬住"又在列表里加一行"）。"""
    from lantai.api import app as app_module

    ids = [id(r) for r in app_module.CORE_ROUTERS]
    dup = {i for i in ids if ids.count(i) > 1}
    assert not dup, f"CORE_ROUTERS 含重复条目 {len(dup)} 个"
