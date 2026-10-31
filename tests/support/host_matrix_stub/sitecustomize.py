"""宿主矩阵 E2E 冒烟的子进程替身（替外部 LLM 网络：embedding + chat）。

背景：E2E 冒烟要求真实子进程（不 mock 进程边界），但 embedding/chat 走外部 API，
子进程无法继承父进程的 monkeypatch。AGENTS.md 测试纪律允许对**外部网络**打替身
（LLM/Embedding/Rerank API），严禁替被测函数内部逻辑。

机制：本目录经 PYTHONPATH 注入子进程，CPython 启动时自动 import sitecustomize，
在此把 `lantai.llm.client.embed` 换成确定性 3-gram 哈希嵌入（与
tests/test_bixiao_deterministic.py 的 `_hash_embed` 同款范式），
并把 `chat_json` 换成确定性意图替身（票 07：hook 首帧预算 SHELL_HOOK_TIMEOUT=2s，
真实 chat 调用 + tenacity 3 次重试退避实测 ~8s，`_run_with_timeout` 掐死
build_context → 注入空；chat 属外部网络，同替身纪律）。
因 `hybrid` / `shell_hook` 均以 `from lantai.llm.client import embed` 导入，
本模块在它们被 import 之前完成替换，故两处引用都拿到替身。

仅当环境变量 LANTAI_TEST_STUB_EMBED=1 时生效——避免影响其他子进程。
"""

import os

if os.environ.get("LANTAI_TEST_STUB_EMBED") == "1":
    import hashlib

    import lantai.llm.client as _client

    _EMBED_DIM = 512

    def _hash_embed(texts: list[str]) -> list[list[float]]:
        """确定性 3-gram 哈希嵌入（外部 embedding 服务的测试替身）。"""
        vecs = []
        for t in texts:
            v = [0.0] * _EMBED_DIM
            t = (t or "").strip()
            grams = [t[i : i + 3] for i in range(len(t) - 2)] if len(t) >= 3 else ([t] if t else [])
            for g in grams:
                idx = int.from_bytes(hashlib.sha256(g.encode("utf-8")).digest()[:4], "big") % (
                    _EMBED_DIM
                )
                v[idx] += 1.0
            vecs.append(v)
        return vecs

    _client.embed = _hash_embed

    def _stub_chat_json(system: str, user: str) -> dict:
        """外部 chat 服务的测试替身：意图分类协议形状（intent.py 期待 `intent` 键）。

        固定返回 fact_lookup（DEFAULT_INTENT 同值）——宿主矩阵冒烟只验证
        检索/注入/回执链路，不验证 LLM 意图分类质量（那属于 eval 域）。
        """
        return {"intent": "fact_lookup", "reason": "stub"}

    _client.chat_json = _stub_chat_json
