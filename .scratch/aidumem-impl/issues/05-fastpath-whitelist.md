# 05 — Fastpath 白名单直写

**What to build:** 实现三类句型（自我声明「我叫X」/ 偏好表达「我喜欢X」/ 显式指令「记住：X」）的 fastpath 直写——绕过 LLM 提取直接写入。3-5 条正则覆盖，放 `parsing/fastpath.py`。precision ≥ 95%，不设 recall（宁 miss 不脏写）。命中后直接返回 `fastpath_candidate`，不入 coalesce 缓冲。

**Blocked by:** 04 — Tidal Coalescing 缓冲

**Status:** ready-for-agent

- [ ] `parsing/fastpath.py` 存在，3-5 条正则覆盖三类句型
- [ ] fastpath 命中返回 `fastpath_candidate`，不入 coalesce 缓冲
- [ ] fastpath 在 coalesce 缓冲前判断
- [ ] 手工标注 50 条测试集，precision ≥ 95%
- [ ] E2E 测试：「我叫张三」直写成功
