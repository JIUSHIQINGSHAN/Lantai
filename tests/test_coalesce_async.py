"""coalesce add_async 测试：幂等 / job_id / 合并统计 / 并发 / 降级同步"""
from unittest.mock import patch

import pytest

from remembrance.ingestion.coalesce import CoalesceBuffer, get_coalesce_buffer


@pytest.fixture
def buf():
    return CoalesceBuffer()


class TestAddAsync:
    def test_job_id_stable(self, buf):
        j1 = buf.job_id("u", "chat", "内容")
        j2 = buf.job_id("u", "chat", "内容")
        assert j1 == j2
        assert len(j1) == 16
        # 不同内容 → 不同 job_id
        assert j1 != buf.job_id("u", "chat", "其他内容")

    def test_idempotent_within_ttl(self, buf):
        r1 = buf.add_async("u", "chat", "内容甲")
        r2 = buf.add_async("u", "chat", "内容甲")
        assert r1["status"] == "queued"
        assert r2["status"] == "queued"
        assert r2["duplicate"] is True
        assert r1["job_id"] == r2["job_id"]
        # 只入队一次
        assert buf.water_level()["total_messages"] == 1

    def test_different_content_both_queued(self, buf):
        buf.add_async("u", "chat", "内容甲")
        buf.add_async("u", "chat", "内容乙")
        assert buf.water_level()["total_messages"] == 2

    def test_seen_expired_by_check_idle(self, buf):
        buf.add_async("u", "chat", "内容甲")
        assert buf.water_level()["seen_fingerprints"] == 1
        # 模拟指纹过期：直接把 _seen 时间改到 TTL 之前
        jid = buf.job_id("u", "chat", "内容甲")
        with buf._lock:
            buf._seen[jid] = 0.0
        buf.check_idle()  # 顺带清理过期指纹
        assert buf.water_level()["seen_fingerprints"] == 0
        # 过期后再入队 → 不再判重
        r = buf.add_async("u", "chat", "内容甲")
        assert r.get("duplicate") is None


class TestFlushStats:
    def test_water_level_reports_flush_stats(self, buf):
        assert buf.water_level()["flush_count"] == 0
        assert buf.water_level()["last_flush_ts"] is None
        # 塞满 max_parts 触发冲刷（general: max_parts=8）
        for i in range(8):
            buf.add("u", "chat", f"消息{i}")
        wl = buf.water_level()
        assert wl["flush_count"] == 1
        assert wl["last_flush_ts"] is not None

    def test_add_returns_count_computed_under_lock(self, buf):
        r = buf.add("u", "chat", "第一条")
        assert r == {"buffered": True, "count": 1}
        r2 = buf.add("u", "chat", "第二条")
        assert r2 == {"buffered": True, "count": 2}


class TestConcurrency:
    def test_concurrent_add_async_same_content_single_queue(self, buf):
        """多线程同内容 add_async → 幂等去重，只入队一次"""
        import threading

        results = []
        lock = threading.Lock()

        def worker():
            r = buf.add_async("u", "chat", "并发内容")
            with lock:
                results.append(r)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 8
        assert sum(1 for r in results if r.get("duplicate")) == 7
        assert buf.water_level()["total_messages"] == 1


class TestServiceFallback:
    def test_add_memory_async_syncs_when_coalesce_disabled(self):
        """COALESCE_ENABLED=false（默认）→ 降级同步，返回 synced + job_id"""
        from remembrance.services import memory_service as ms
        from remembrance.models.schemas import AddMemoryReq

        req = AddMemoryReq(title="t", content="这是一段足够长的内容")
        fake = {"document_id": "doc1", "candidate_id": "c1"}
        with patch.object(ms.settings, "COALESCE_ENABLED", False), \
             patch.object(ms, "add_memory", return_value=fake) as m:
            res = ms.add_memory_async(req)
        m.assert_called_once_with(req)
        assert res["status"] == "synced"
        assert res["document_id"] == "doc1"
        assert len(res["job_id"]) == 16

    def test_add_memory_async_queues_when_enabled(self):
        from remembrance.services import memory_service as ms
        from remembrance.models.schemas import AddMemoryReq

        buf = CoalesceBuffer()
        req = AddMemoryReq(title="t", content="这是一段足够长的内容")
        with patch.object(ms.settings, "COALESCE_ENABLED", True), \
             patch.object(ms, "get_coalesce_buffer", return_value=buf), \
             patch.object(ms, "add_memory") as m:
            res = ms.add_memory_async(req)
        m.assert_not_called()  # 入队路径不落库
        assert res["status"] == "queued"
        assert len(res["job_id"]) == 16

    def test_add_memory_async_persists_on_flush(self):
        """入队即触发冲刷（缓冲满）→ combined_content 持久化，不静默丢弃"""
        from remembrance.services import memory_service as ms
        from remembrance.models.schemas import AddMemoryReq

        buf = CoalesceBuffer()
        with patch.object(ms.settings, "COALESCE_ENABLED", True), \
             patch.object(ms, "get_coalesce_buffer", return_value=buf), \
             patch.object(ms, "_create_candidate_with_extraction",
                          return_value={"document_id": "doc1",
                                        "candidate_id": "c1"}) as m:
            last = None
            # general profile max_parts=8；不同内容 → 互不去重，第 8 条触发冲刷
            for i in range(8):
                last = ms.add_memory_async(AddMemoryReq(
                    title="t", content=f"这是第{i}条足够长的异步内容"))
        assert last["status"] == "flushed"
        assert last["document_id"] == "doc1"
        assert m.call_count == 1
        combined = m.call_args.args[0].content
        assert "这是第0条" in combined and "这是第7条" in combined

    def test_add_memory_async_flush_failure_forgets_fingerprint(self):
        """持久化失败 → 清除指纹，允许重试（不丢数据）"""
        from remembrance.services import memory_service as ms
        from remembrance.models.schemas import AddMemoryReq

        buf = CoalesceBuffer()
        with patch.object(ms.settings, "COALESCE_ENABLED", True), \
             patch.object(ms, "get_coalesce_buffer", return_value=buf), \
             patch.object(ms, "_create_candidate_with_extraction",
                          side_effect=RuntimeError("llm down")):
            with pytest.raises(RuntimeError):
                for i in range(8):
                    ms.add_memory_async(AddMemoryReq(
                        title="t", content=f"这是第{i}条足够长的异步内容"))
        # 指纹已清除 → 同内容可重新提交
        jid = buf.job_id("default", "general", "这是第7条足够长的异步内容")
        assert jid not in buf._seen


class TestIdleConsumer:
    def test_run_coalesce_idle_persists(self):
        """空闲冲刷 → run_coalesce_idle 持久化 combined_content"""
        from remembrance.workers.ingest_worker import run_coalesce_idle
        from remembrance.services import memory_service as ms

        buf = CoalesceBuffer()
        buf.add("u", "general", "第一条足够长的异步内容")
        buf.add("u", "general", "第二条足够长的异步内容")
        with buf._lock:
            buf._timestamps["u:general"] = 0.0  # 空闲超时已到

        with patch("remembrance.ingestion.coalesce.get_coalesce_buffer",
                   return_value=buf), \
             patch.object(ms, "_create_candidate_with_extraction",
                          return_value={"document_id": "doc1"}) as m:
            run_coalesce_idle()
        assert m.call_count == 1
        combined = m.call_args.args[0].content
        assert "第一条" in combined and "第二条" in combined
        assert buf.water_level()["total_messages"] == 0  # 已冲刷且持久化

    def test_run_coalesce_idle_requeues_on_failure(self):
        """持久化失败 → 该批消息锁内恢复，不静默丢失也不二次冲刷"""
        from remembrance.workers.ingest_worker import run_coalesce_idle
        from remembrance.services import memory_service as ms

        buf = CoalesceBuffer()
        # 多消息（接近 max_parts）验证恢复路径不触发二次冲刷
        for i in range(7):
            buf.add("u", "general", f"消息{i}足够长的异步内容")
        with buf._lock:
            buf._timestamps["u:general"] = 0.0

        with patch("remembrance.ingestion.coalesce.get_coalesce_buffer",
                   return_value=buf), \
             patch.object(ms, "_create_candidate_with_extraction",
                          side_effect=RuntimeError("llm down")):
            run_coalesce_idle()  # 不抛——异常被吞并恢复
        assert buf.water_level()["total_messages"] == 7  # 全部恢复在缓冲
        assert buf.water_level()["active_keys"] == 1

    def test_requeue_does_not_retrigger_flush(self):
        """requeue 只恢复缓冲，不触发 max_parts 立即冲刷"""
        buf = CoalesceBuffer()
        items = [{"content": f"内容{i}足够长", "title": "", "ts": 0.0}
                 for i in range(8)]
        buf.requeue("u:general", items)  # 8 条 = max_parts，也不应冲刷
        assert buf.water_level()["total_messages"] == 8
        assert buf.water_level()["active_keys"] == 1
