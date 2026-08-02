# aiduMEM 移植结果文档

**项目**: Remembrance-System v0.3.0
**日期**: 2026-08-02
**范围**: aiduMEM 六组设计思想移植——从审计到实施全流程

---

## 一、执行摘要

本次工作完成了 Remembrance-System 从 v0.2.0 到 v0.3.0 的全面升级，吸收 aiduMEM 的六组设计思想：写入节流、全链可观测、数据自治、可插拔集成、克隆即跑、架构有纪律。

通过 wayfinder 技能完成了 16 张决策票据的全流程审议，通过 to-spec/to-tickets 技能合成了 47 条 User Story 和 14 张实施票据，最终用 TDD 方式逐张实施。

### 核心数字

| 指标 | 数值 |
|------|------|
| 决策票据（Phase 2） | 16/16 resolved |
| 实施票据（Phase 4-5） | 14/14 resolved |
| ADR 产出 | 7 份（0001-0007） |
| 新增/修改 Python 模块 | 44 个（git diff 2966b3d..HEAD 非测试 .py 实测） |
| 新增测试 | 39 个（14+5+15+3+2），全绿 |
| 全量测试通过率 | **118/118 全绿（v0.3.5 起零失败）** |
| 零回归 | ✅ |

---

## 二、Phase 1-2：审计与决策（Wayfinding）

### 审计发现

v0.2.0 存在 6 类问题：
1. 写入无节流——每条短消息触发一次 LLM 提取
2. 全链不可观测——检索是黑盒
3. 数据无自治——记忆只增不删，重复堆积
4. 集成无标准——无 CLI 注入或 MCP
5. 部署无工程化——无 Docker/CI
6. 代码有硬编码——P0 bug（DEFAULT_LANE 缺失）、死代码残留

### 决策记录（16 张票据 → 7 份 ADR）

| ADR | 主题 | 决策 |
|-----|------|------|
| 0001 | 门面铁律 | 只搬家不改语义，旧 import 全绿；service 层下沉 |
| 0002 | 零硬编码 | REMEMBRANCE_HOME + __file__ 自解析；validate() 只 warn；删 VECTOR_DIMENSION |
| 0003 | coalesce 缓冲键 | user_id + lane；LANE_COALESCE_PROFILES；COALESCE_ENABLED 默认 false |
| 0004 | 基础设施栈 | 保留 ChromaDB；jieba 中文分词；统一 bge-m3；不引入 mem0/Qdrant |
| 0005 | 遗忘语义 | 只降权不删；archived 不参与检索；不做 GC |
| 0006 | Shell Hook 契约 | stdin/stdout JSON；2s 超时；≤3 字符不注入；top_k=5 无 rerank |
| 0007 | 集成形态 | Shell Hook（读）+ MCP server（写）并存 |

---

## 三、Phase 3-5：规格合成与实施

### 实施顺序与依赖图

```
01 ─┬─→ 02 ─┬─→ 04 ──→ 05
    │       ├─→ 06 ──→ 08
    │       └─→ 09 ──→ 10
    └─→ 03
   
04 ──→ 07 ──→ 13 ──→ 14
06 ──→ 11 ──→ 12
```

### 票据清单与交付物

| # | 标题 | 交付物 | 测试 |
|---|------|--------|------|
| 01 | P0 修复 + 零硬编码 | `settings.py`: DEFAULT_LANE, REMEMBRANCE_HOME, validate_config(), 删 VECTOR_DIMENSION | 14 tests |
| 02 | 基础设施栈 | 删 embedding 列, jieba BM25, bge-m3, cosine ChromaDB | 5 tests |
| 03 | Service 层 | `services/memory_service.py`, `evolution_service.py`, `source_service.py`; auth 死代码删除 | 43 E2E 无回归 |
| 04 | Tidal Coalescing | `ingestion/coalesce.py`: CoalesceBuffer, LANE_COALESCE_PROFILES, COALESCE_ENABLED | 3 tests |
| 05 | Fastpath 白名单 | `parsing/fastpath.py`: 三类句型正则, precision≥95%, 缓冲前判断 | 5 tests |
| 06 | search_trace | `hybrid.py`: trace 参数, 每步 {step, elapsed_ms, candidate_count, score_range} | 2 tests |
| 07 | Health + stats | `/health`, `/health/deep`, `/stats` 三端点, 公开/需Key 分离 | 3 tests |
| 08 | 性能基线工具 | `scripts/perf_baseline.py`: aiduMEM 50 问样本, P50/P95 延迟 | 脚本 |
| 09 | 遗忘语义 | `forgetting.py`: ARCHIVE_DECAY_THRESHOLD 自动归档, 搜索 WHERE status='active' | 隐式测试 |
| 10 | 余弦去重 | `gate/dedup.py`: 余弦相似度, candidate 创建时 gate 前, merge/update | 模块 |
| 11 | Shell Hook | `scripts/shell_hook.py`: stdin/stdout JSON, 2s 超时, Markdown 列表 | 脚本 |
| 12 | MCP server | `scripts/mcp_server.py`: search/add/feedback 三 tool, JSON-RPC | 脚本 |
| 13 | Docker + GH Actions | `Dockerfile` 多阶段构建, `.github/workflows/ci.yml` tag→GHCR | 配置 |
| 14 | 运维脚本 | `scripts/backup.py`, `restore.py`, `upgrade_check.py`, `reextract.py` | 脚本 |

---

## 四、新增模块架构

```
remembrance/
├── core/
│   ├── settings.py          ← T01: REMEMBRANCE_HOME, validate_config, DEFAULT_LANE
│   └── auth.py              ← T03: 死代码清理
├── ingestion/
│   └── coalesce.py          ← T04: Tidal Coalescing 缓冲器（NEW）
├── parsing/
│   ├── extractor.py         ← 已有
│   └── fastpath.py          ← T05: 白名单直写（NEW）
├── gate/
│   └── dedup.py             ← T10: 余弦去重（NEW）
├── retrieval/
│   └── hybrid.py            ← T02/T06/T09: jieba + trace + status filter
├── memory/
│   └── forgetting.py        ← T09: 自动归档
├── services/                ← T03: service 层（NEW）
│   ├── memory_service.py
│   ├── evolution_service.py
│   └── source_service.py
└── api/
    ├── routes_search.py     ← T06: ?trace=true
    └── routes_health.py     ← T07: /health/deep + /stats
scripts/
├── perf_baseline.py         ← T08（NEW）
├── shell_hook.py            ← T11（NEW）
├── mcp_server.py            ← T12（NEW）
├── backup.py                ← T14（NEW）
├── restore.py               ← T14（NEW）
├── upgrade_check.py         ← T14（NEW）
└── reextract.py             ← T14（NEW）
```

---

## 五、测试统计

| 测试文件 | 测试数 | 状态 |
|----------|--------|------|
| `test_settings.py` | 14 | ✅ 全绿 |
| `test_infra.py` | 5 | ✅ 全绿 |
| `test_features.py` | 15 | ✅ 全绿 |
| `test_dedup.py` | 3 | ✅ 全绿 |
| `test_shell_hook.py` | 2 | ✅ 全绿 |
| `test_e2e.py` | 18 | ✅ 全绿 |
| `test_auth.py` | 9 | ✅ 全绿（v0.3.2 新增 3 个绑定安全测试） |
| `test_prefilter.py` | 18 | ✅ 全绿（v0.3.5 修复热缓存测试隔离 + 新增热缓存行为测试） |
| `test_p0.py` | 7 | ✅ 全绿（v0.3.2 重写为 fts/hybrid 层单元测试） |
| `test_reranker.py` | 8 | ✅ 全绿 |
| `test_ssrf.py` | 4 | ✅ 全绿（v0.3.3 新增，SSRF 防护） |
| `test_ops.py` | 5 | ✅ 全绿（v0.3.3 新增，备份/恢复加固） |
| `test_mcp.py` | 6 | ✅ 全绿（v0.3.3 新增，MCP 协议校验） |
| `test_fts_integration.py` | 4 | ✅ 全绿（v0.3.4 新增，FTS 同步与融合） |
| **总计** | **118** | **118✅ 0❌ 全绿** |

v0.3.5：test_prefilter 的 2 个"预存失败"实为测试隔离缺陷——prefilter 15s 热缓存（设计行为）被文件内测试顺序串味；加 autouse fixture（monkeypatch 模块属性）隔离 + 新增 2 个热缓存行为测试。至此全量测试首次全绿。

2 个预存 bug（非 v0.3.2 引入），经基线 d579d35 复现确认：
- `test_prefilter.py`: 短查询/随机查询行为变化（v0.2.0 遗留，另立项）

---

## 六、ADR 索引

| 文件 | 主题 |
|------|------|
| `docs/adr/0001-facade-rule.md` | 门面铁律 |
| `docs/adr/0002-zero-hardcoding.md` | 零硬编码 |
| `docs/adr/0003-coalesce-buffer-key.md` | coalesce 缓冲键 |
| `docs/adr/0004-infra-stack.md` | 基础设施栈 |
| `docs/adr/0005-forgetting-semantics.md` | 遗忘语义 |
| `docs/adr/0006-shell-hook-contract.md` | Shell Hook 契约 |
| `docs/adr/0007-mcp-form.md` | 集成形态 |

---

## 七、保留事项（Fog）

以下三项为可选增强，不阻塞任何实施路径：

1. **salience 冲突降权**——反义词对碰撞与 contradiction gate 整合
2. **autodream 7 天周期蒸馏**——记忆定期压缩总结
3. **checkpoint 五段会话快照**——aiduMEM checkpoint.py 移植

---

## 八、Git 提交历史

| Commit | 范围 |
|--------|------|
| `960d1d3` | T01: P0 fix + zero-hardcoding settings |
| `c06d040` | T02: infra stack (ChromaDB/jieba/bge-m3/cosine) |
| `3cdc958` | T03: service layer + dead code cleanup |
| (latest) | T04-T14: coalesce, fastpath, trace, health, perf, forgetting, dedup, shell_hook, MCP, Docker, ops |

---

## 九、后续建议

1. **修复预存测试 bug**——`test_p0.py` 的 `resp.json` → `resp.json()` + mock ChromaDB
2. **coalesce 参数校准**——用 `scripts/perf_baseline.py` 跑基线后按 lane 差异化
3. **去重阈值标定**——用 bge-m3 对中文样本跑余弦，实测 0.80/0.65 是否合适
4. **arm64 Docker 镜像**——当前仅 amd64，后续加 arm64
5. **Fog 出票**——salience / autodream / checkpoint 三项后续决策

---

## v0.3.1 审计修复

基于 `docs/aidumem-port-results.md` 独立审计报告（⚠️ 有条件通过），执行 7 Wave 修复：

| Wave | Commit | 范围 |
|------|--------|------|
| 1 | `f9f428c` | pyproject deps+version, validate_config API_KEY warn, backup timestamp dir, Dockerfile real wheel |
| 2 | `40f330d` | fastpath chain via evolve_worker in_, wire dedup three-state, coalesce idle job, /stats worker times |
| 3 | `e8fab1f` | shell_hook 2s hard timeout, standard MCP protocol, upgrade_check.py, restore stop-guard |
| 4 | `a31bf87` | sink checkpoint/edges logic to service layer |
| 5 | `951e238` | dedup/forgetting/shell_hook tests, fix hollow trace test |
| 6 | (this commit) | correct results doc numbers, resolve tickets 04-14, update glossary |

修复后全量测试：95 passed / 2 failed（2 个预存 bug 不变，v0.3.2 进一步收敛）。

## v0.3.2 P0 修复

基于 `v0.3.1-audit-report.md` 三项 P0：

| Wave | Commit | 范围 |
|------|--------|------|
| 1 | `41e0ad1` | 生成物移出 Git（.venv-audit/.chromadb/egg-info），.gitignore 收紧 |
| 2 | `41e08c2` | 默认回环绑定 + 非回环强制 API_KEY + 恒时比较（hmac） |
| 3 | (见 git log) | test_p0 重写为单元测试；修复 FTS schema、Chronos 时区、BM25 ptp 三个潜在 bug |

附带记录：FTS5 当前零生产调用方（仅 init，未接入写入/检索链路），另立项决策接入或移除。执行方案见 `docs/plans/v0.3.2-p0-remediation-plan.md`。

## v0.3.3 P1 修复

基于 `v0.3.1-audit-report.md` P1 项：

| Wave | Commit | 范围 |
|------|--------|------|
| 1 | `ed90b24` | SSRF 防护：RSS 抓取协议白名单 + 私网/回环阻断 + 限长 |
| 2 | `fa98572` | 备份 manifest + online backup；恢复路径限定 + hash 校验 + 原子换入 + fail-closed |
| 3 | `701c903` | MCP 输入校验 + JSON-RPC 错误隔离；SearchReq 限界；/stats SQL 聚合；/health/deep 空 key 跳过 |

新增测试：test_ssrf 4 / test_ops 5 / test_mcp 6，全部通过。执行方案见 `docs/plans/v0.3.3-p1-remediation-plan.md`。

## v0.3.4 FTS5 接入（ADR-0008 实施）

| Wave | Commit | 范围 |
|------|--------|------|
| 1 | `c2c4c57` | FTS5 同事务写入同步（promoter 4 点）+ 旧 schema 自动迁移 + 检索权重进 settings |
| 2 | `70a3ec3` | hybrid 融合打分（0.6/0.25/0.05/0.1）+ FTS 追加召回 + BM25 语料缓存（M4） |
| 3 | (见 git log) | test_fts_integration 4 测试 + 文档 |

新增测试：test_fts_integration 4 个全绿。已知限制（ADR-0008）：trigram 对 2 字中文查询无效（测试已用 ≥3 字查询词规避）。
