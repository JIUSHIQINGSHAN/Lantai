"""票 `.scratch/migration-chain/01`：迁移链中途失败不得静默跳过后续全部迁移。

实证根因（`.scratch/readside-gaps/probe_24_chain_break.py`，子进程隔离）：

```
=== 对照：不注入失败 ===
  RESULT version=26 tables=10 reflect_run=True checkpoint=True mn_user_id=True
=== 实验：在 v9 块收尾前注入一次异常 ===
  RESULT version=8  tables=4  reflect_run=False checkpoint=False mn_user_id=False
```

v9 一步失败，v10–v26 十八次迁移全部缺席，而服务照常启动、日志只有一行
error。`PRAGMA user_version = N` 写在块尾，抛在它之前 version 就停在 N-1，
后面所有 `if user_version < M` 全部不成立。

约束：**不能改成"迁移失败就拒绝启动"**。fts-availability/01 的判断标准在
这里同样成立——一个非关键索引建不起来不该让整个服务起不来。要的是
「不阻断启动 + 失败可见 + 失败位置明确」三件事同时成立。

全部不 mock：真实临时 SQLite 库 + 真实 apply_migrations + 真实 /health/deep。
"""

import sqlite3

from lantai.storage.db import TARGET_SCHEMA_VERSION, get_migration_failures, init_db

# ── 构造工具 ────────────────────────────────────────────────────


def _fresh(tmp_path, monkeypatch):
    """把 engine 指到一个空临时库，返回 (url, db_path)。"""
    db_path = tmp_path / "m.db"
    url = f"sqlite:///{db_path.as_posix()}"
    monkeypatch.setattr("lantai.core.settings.settings.DATABASE_URL", url)
    import lantai.storage.db as db_mod

    monkeypatch.setattr(db_mod, "engine", db_mod.create_engine(url))
    return url, db_path


def _set_version(db_path, v: int) -> None:
    c = sqlite3.connect(str(db_path))
    try:
        c.execute(f"PRAGMA user_version = {int(v)}")
        c.commit()
    finally:
        c.close()


def _tables(db_path) -> set[str]:
    c = sqlite3.connect(str(db_path))
    try:
        return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        c.close()


def _has_col(db_path, table: str, col: str) -> bool:
    c = sqlite3.connect(str(db_path))
    try:
        return col in {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
    finally:
        c.close()


def _poison_v9(monkeypatch):
    """让 v9 块的「建 memorynode 表」语句抛一次异常，其余放行。

    这是复现实证到的那一个断点：抛在 v9 收尾 `user_version = 9` 之前，
    于是 v10..v26 在旧实现里全部静默跳过。返回还原函数。
    """
    import lantai.storage.db as db_mod

    class Boom(Exception):
        pass

    class PoisonedConn:
        def __init__(self, inner):
            self._inner = inner
            self._fired = False

        def execute(self, sql, *a, **k):
            if (not self._fired) and "CREATE TABLE IF NOT EXISTS memorynode" in str(sql):
                self._fired = True
                raise Boom("票01 测试注入：v9 建表失败")
            return self._inner.execute(sql, *a, **k)

        def commit(self):
            return self._inner.commit()

        def close(self):
            return self._inner.close()

        def __getattr__(self, name):
            return getattr(self._inner, name)

    real_raw = db_mod.engine.raw_connection
    monkeypatch.setattr(
        db_mod.engine, "raw_connection", lambda *a, **k: PoisonedConn(real_raw(*a, **k))
    )
    return db_mod, PoisonedConn, real_raw


def _version(db_path) -> int:
    c = sqlite3.connect(str(db_path))
    try:
        return c.execute("PRAGMA user_version").fetchone()[0]
    finally:
        c.close()


# ── 1. 逐块隔离：一块失败，后面的块仍要跑 ──────────────────────


class TestChainIsolation:
    def test_target_version_is_single_source(self):
        """必须有单一真源说明「应该跑到第几版」。

        没有它就没法判断链是否跑到头——现在最高版本硬编码散落在 25 个
        `if user_version < N` 里，没有任何地方能回答「现在到几了」。
        """
        assert isinstance(TARGET_SCHEMA_VERSION, int)
        assert TARGET_SCHEMA_VERSION >= 26

    def test_one_block_failure_does_not_skip_later_blocks(self, tmp_path, monkeypatch):
        """决定性：v9 抛异常后，v10..v26 的产物必须仍然存在。

        这是整张票的核心。修前 version 停在 8、reflect_run 表不存在；
        修后 version 必须是 26，且 v10 之后的表都在。

        注入方式：patch `engine.raw_connection` 返回一个包装连接，对含
        `CREATE TABLE IF NOT EXISTS memorynode` 的 execute 抛一次——
        那是 v9 块的建表语句，抛在它之后 v9 的收尾 `user_version = 9`
        就执行不到，正是复现实证到的那个断点。
        """
        url, db_path = _fresh(tmp_path, monkeypatch)
        db_mod, PoisonedConn, real_raw = _poison_v9(monkeypatch)
        db_mod.SQLModel.metadata.create_all(db_mod.engine)
        _set_version(db_path, 8)

        try:
            db_mod.apply_migrations(PoisonedConn(real_raw()))
        finally:
            monkeypatch.setattr(db_mod.engine, "raw_connection", real_raw)

        tabs = _tables(db_path)
        # v10 的表、v12 的表、v26 的列都必须齐——它们排在 v9 后面
        assert "reflect_run" in tabs, "v9 一挂，v10 的表也没了（后续块被跳过）"
        assert "session_checkpoint" in tabs, "v12 的表被跳过"
        assert _has_col(db_path, "memorynode", "user_id"), "v26 的归属列被跳过"

    def test_failure_is_recorded_with_location(self, tmp_path, monkeypatch):
        """决定性：失败必须被记下来，且记的是「哪一跳、什么原因」。

        修前只有一行 logger.error，进程内无任何状态可查——运维只能去
        翻日志。修后 get_migration_failures() 要能报出来。
        """
        url, db_path = _fresh(tmp_path, monkeypatch)
        db_mod, PoisonedConn, real_raw = _poison_v9(monkeypatch)
        db_mod.SQLModel.metadata.create_all(db_mod.engine)
        _set_version(db_path, 8)

        try:
            db_mod.apply_migrations(PoisonedConn(real_raw()))
        finally:
            monkeypatch.setattr(db_mod.engine, "raw_connection", real_raw)

        failures = get_migration_failures()
        assert failures, "迁移失败了却没有任何记录——修前只有一行 logger.error"
        assert any(f.get("version") == 9 for f in failures), f"记录里没有失败跳的版本号：{failures}"
        assert any(
            "v9" in str(f.get("error", "")) or "memorynode" in str(f.get("error", ""))
            for f in failures
        ), f"记录里没有失败原因：{failures}"

    def test_no_failure_means_no_records(self, tmp_path, monkeypatch):
        """对偶：正常跑完不该有失败记录——否则健康检查永远红。"""
        url, db_path = _fresh(tmp_path, monkeypatch)

        import lantai.storage.db as db_mod

        db_mod.init_db()
        assert get_migration_failures() == []

    def test_failed_block_does_not_advance_version(self, tmp_path, monkeypatch):
        """决定性：失败的块不得推进 version，且后续块必须把 version 带到位。

        光看最终 version 杀不掉这个变异——失败分支也写 version 时，最终
        值照样是 TARGET、failures 照样是 [9]，两条断言全绿。必须观察
        **pragma 写入序列**：失败的 v9 之后紧接着的写入必须是 v10，
        中间不得出现 v9。

        另一半同样重要：最终 version 必须到 TARGET，证明后续块真的跑了。
        """
        url, db_path = _fresh(tmp_path, monkeypatch)
        db_mod, PoisonedConn, real_raw = _poison_v9(monkeypatch)
        db_mod.SQLModel.metadata.create_all(db_mod.engine)
        _set_version(db_path, 8)

        # 记录每次 PRAGMA user_version 写入的版本号
        writes: list[int] = []
        real_conn = PoisonedConn(real_raw())

        orig_execute = real_conn.execute

        def spy_execute(sql, *a, **k):
            text = str(sql)
            if text.startswith("PRAGMA user_version = "):
                writes.append(int(text.rsplit("=", 1)[1].strip()))
            return orig_execute(sql, *a, **k)

        real_conn.execute = spy_execute  # type: ignore[method-assign]

        try:
            db_mod.apply_migrations(real_conn)
        finally:
            monkeypatch.setattr(db_mod.engine, "raw_connection", real_raw)

        # 后续块把 version 带到了目标值
        assert _version(db_path) == TARGET_SCHEMA_VERSION, (
            f"version 只到 {_version(db_path)}，后续块没跑完"
        )
        # 失败的那一跳确实被记下了（不是假成功）
        assert [f["version"] for f in get_migration_failures()] == [9]
        # 关键：失败的 v9 之后，pragma 写入序列里不得出现 9
        assert 9 not in writes, f"失败的块也推进了 version，pragma 写入序列：{writes}"
        assert writes == sorted(writes), f"version 写入顺序乱了：{writes}"
        assert writes and writes[-1] == TARGET_SCHEMA_VERSION, (
            f"最后一次写入不是目标版本：{writes[-3:]}"
        )

    def test_rerun_from_mid_version_reaches_target(self, tmp_path, monkeypatch):
        """从中间版本起跑必须到达目标版本（幂等性 + 快照推进的双重保障）。

        **为什么快照不推进也不会被这条杀掉**：`if user_version >= target`
        用的是快照，快照停在旧值（更小）时判断为 False——块会**重复执行**
        而不是被跳过。25 个块都幂等（`IF NOT EXISTS` / `_has_column` 守卫），
        重复执行无害，所以「不推进快照」是等价变异，杀不掉也不该假装杀掉。

        这条真正锁的是：从 v8 起跑（这是真实库里出现过的状态）能一路补到
        TARGET，且不因重复执行任何一块而报错。快照推进是性能优化
        （少跑 18 个幂等块），不是正确性前提——想通这一点才没去写一条
        自欺欺人的测试。
        """
        url, db_path = _fresh(tmp_path, monkeypatch)

        import lantai.storage.db as db_mod

        db_mod.SQLModel.metadata.create_all(db_mod.engine)
        _set_version(db_path, 8)
        db_mod.apply_migrations(db_mod.engine.raw_connection())

        assert _version(db_path) == TARGET_SCHEMA_VERSION, f"从 v8 起跑只到 v{_version(db_path)}"
        # 幂等：再跑一遍不得报错、不得有失败记录
        db_mod.apply_migrations(db_mod.engine.raw_connection())
        assert _version(db_path) == TARGET_SCHEMA_VERSION
        assert get_migration_failures() == [], f"重复执行产生了失败：{get_migration_failures()}"


# ── 2. 可见出口：/health/deep 必须报迁移状态 ────────────────────


class TestHealthReportsMigrations:
    def test_health_deep_has_migrations_check(self, tmp_path, monkeypatch):
        """决定性：/health/deep 必须有一项报迁移链状态。

        修前 checks 只有 sqlite/chromadb/fts/llm——迁移坏了半个月没人知道，
        直到某个功能 500 才顺藤摸到这里。同 fts-availability/01 的形状。
        """
        url, db_path = _fresh(tmp_path, monkeypatch)

        import lantai.storage.db as db_mod

        db_mod.init_db()

        # 直调 handler，不走 TestClient——它的 lifespan 会再跑一次 init_db，
        # 把人为压低的 version 补回目标值，被测的正是「补不回来时」的分支。
        from lantai.api.routes_health import health_deep

        out = health_deep()

        checks = out["checks"]
        assert "migrations" in checks, f"checks 里没有 migrations 项：{sorted(checks)}"

    def test_health_deep_reports_fail_when_stuck(self, tmp_path, monkeypatch):
        """决定性：version 没跑到头时必须报 fail，且附原因。

        修前 version 停在中间也返回 ok——这正是「坏了没人知道」的直接原因。
        """
        url, db_path = _fresh(tmp_path, monkeypatch)

        import lantai.storage.db as db_mod

        db_mod.init_db()
        _set_version(db_path, 8)  # 人为压回中间版本

        # 直调 handler，不走 TestClient——它的 lifespan 会再跑一次 init_db，
        # 把人为压低的 version 补回目标值，被测的正是「补不回来时」的分支。
        from lantai.api.routes_health import health_deep

        out = health_deep()

        checks = out["checks"]
        assert checks.get("migrations", "").startswith("fail"), (
            f"迁移卡在中间版本却报 ok：{checks.get('migrations')}"
        )
        assert out["ok"] is False, "有 fail 项时总体 ok 不该是 True"

    def test_health_deep_reports_fail_when_block_failed(self, tmp_path, monkeypatch):
        """决定性：有失败记录时必须报 fail，并带上失败跳的版本与原因。

        这条与上一条是**两个不同分支**：上一条是「version 落后」，这一条是
        「version 已到目标但过程中有块失败过」（逐块隔离后完全可能）。
        少了这条，把 `if failures:` 改成恒假，上一条照样能杀——两条一起
        才锁住 health 的两个 fail 来源。
        """
        url, db_path = _fresh(tmp_path, monkeypatch)
        db_mod, PoisonedConn, real_raw = _poison_v9(monkeypatch)
        db_mod.SQLModel.metadata.create_all(db_mod.engine)
        _set_version(db_path, 8)

        try:
            db_mod.apply_migrations(PoisonedConn(real_raw()))
        finally:
            monkeypatch.setattr(db_mod.engine, "raw_connection", real_raw)

        assert _version(db_path) == TARGET_SCHEMA_VERSION, "前置条件：version 已到目标"
        assert get_migration_failures(), "前置条件：有失败记录"

        from lantai.api.routes_health import health_deep

        out = health_deep()

        msg = out["checks"].get("migrations", "")
        assert msg.startswith("fail"), f"有失败记录却报 {msg!r}"
        assert "v9" in msg, f"报错里没带失败跳的版本号：{msg}"
        assert out["ok"] is False, "有 fail 项时总体 ok 不该是 True"

    def test_health_deep_ok_when_at_target(self, tmp_path, monkeypatch):
        """对偶：跑到头了必须报 ok——不能把正常库报成故障。"""
        url, db_path = _fresh(tmp_path, monkeypatch)

        import lantai.storage.db as db_mod

        db_mod.init_db()

        # 直调 handler，不走 TestClient——它的 lifespan 会再跑一次 init_db，
        # 把人为压低的 version 补回目标值，被测的正是「补不回来时」的分支。
        from lantai.api.routes_health import health_deep

        out = health_deep()

        assert out["checks"].get("migrations") == "ok"


# ── 3. 不阻断启动这一既定行为不得改变 ──────────────────────────


class TestFailureDoesNotBlockStartup:
    def test_init_db_returns_despite_block_failure(self, tmp_path, monkeypatch):
        """决定性：一块失败时 init_db 必须正常返回（不抛）。

        这是本票的硬约束——改成「拒绝启动」会让一个非关键索引建不起来
        就整个服务起不来，比现状更糟。fts-availability/01 的判断标准。
        """
        url, db_path = _fresh(tmp_path, monkeypatch)
        db_mod, PoisonedConn, real_raw = _poison_v9(monkeypatch)
        db_mod.SQLModel.metadata.create_all(db_mod.engine)
        _set_version(db_path, 8)

        try:
            db_mod.apply_migrations(PoisonedConn(real_raw()))
        finally:
            monkeypatch.setattr(db_mod.engine, "raw_connection", real_raw)
