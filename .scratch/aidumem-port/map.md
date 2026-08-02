# aiduMEM 移植 Map

## Destination

Remembrance-System 吸收 aiduMEM 的六组优点：写入节流、全链可观测、数据自治、可插拔集成、克隆即跑、架构有纪律——以逐案确定的技术栈落地，每个特性簇有 spec、有测试、有审查。

## Notes

- **参照系**：aiduMEM 代码在 `C:\Users\Asus\Desktop\aiduMEM`（只读参照，不 import、不复制 license 不兼容的代码——移植的是设计思想与参数区间，代码自己写）
- **设计文档**：`docs/plans/aidumem-port-skill-workflow.md`（本流程的完整设计，含技能调用链和特性簇顺序）
- **每个会话先读**：`docs/agents/domain.md`、`CONTEXT.md`（若已由 /domain-modeling 创建）
- **术语遵循**：`CONTEXT.md` 词汇表（lane / tier / gate / coalesce…）
- **所有决策票据遵循「技术栈逐案」原则**——不在建图阶段预设答案
- **实施顺序**：地基（16 门面 + 13 零硬编码）→ A 写入节流（02·03·04）→ B 可观测（05·06·07）→ C 数据治理（08·09）→ D 集成部署（10·11·12）→ E 工程化收尾（14·15）

## Decisions so far

<!-- 索引——每张已关闭票据一行：gist + 链接 -->

- [性能基线工具形态](issues/07-perf-baseline-tool.md) — 直接移植 aiduMEM 50 问中文中性样本，适配 POST /search + lanes；初期只跑 Test 1，Test 2/3 依赖票据 05/10
- [兼容门面策略](issues/16-compat-facade-strategy.md) — 门面铁律确立（只搬家不改语义，旧 import 全绿）；api_server 保持现状，路由 handler 业务逻辑下沉 service 层；auth.py 三个死代码全删；/improve-codebase-architecture 跳过
- [零硬编码](issues/13-zero-hardcoding.md) — settings 内部不加前缀；外部变量加 REMEMBRANCE_HOME/API_BASE；保持 .env+.gitignore 不引入 .sf_key；__file__ 自解析仓库根；轻量 validate() 只 warn 不 crash；补 DEFAULT_LANE 修 P0 bug；删 VECTOR_DIMENSION 让 ChromaDB 自推断
- [coalesce 缓冲键设计](issues/02-coalesce-buffer-key.md) — 缓冲键 user_id+lane（不引入 session）；lane 替代三档 profile（新增 LANE_COALESCE_PROFILES）；/add 开关切换（COALESCE_ENABLED 默认 false 向后兼容），一个入口自动分流
- [基础设施栈逐案](issues/01-infra-stack-per-case.md) — 保留 ChromaDB 消除冗余；不引入 mem0；jieba 中文分词；统一 bge-m3 embedding
- [coalesce 冲刷参数校准](issues/03-coalesce-flush-params.md) — aiduMEM 50 问句模拟输入；测合并率/批大小/P50P95；初版全用默认值后差异化
- [fastpath 白名单](issues/04-fastpath-whitelist.md) — 三类句型（自我声明/偏好/显式指令）正则匹配放 parsing/fastpath.py；precision≥95% 不设 recall；缓冲前判断
- [search_trace 结构](issues/05-search-trace-structure.md) — 数组每步{step,elapsed_ms,candidate_count,score_range}；?trace=true 按需开启 overhead<1ms；不开独立端点
- [健康探针与 stats](issues/06-health-stats-endpoints.md) — /health 简单公开+/health/deep 查依赖需Key+/stats 记忆分布+coalesce水位+worker时间 需Key；三端点
- [遗忘语义](issues/08-forgetting-semantics.md) — 只降权不删行 archived 不参与检索；保持现有 lane_strength 衰减；不做 GC 永不删除；搜索 WHERE status=active
- [Jaccard 三态去重](issues/09-jaccard-dedup-thresholds.md) — 用余弦不用 Jaccard；预测阈值调低至 0.80/0.65 需实测；去重在 candidate 创建时 gate 之前
- [Shell Hook 契约](issues/10-shell-hook-contract.md) — 照搬 aiduMEM JSON 形状；2s 超时返回空{}；≤3字符不注入；top_k=5 不开 rerank；Markdown 列表带分数
- [MCP server 形态](issues/11-mcp-server-form.md) — 两者并存：Shell Hook 读+MCP server 写（search/add/feedback 三 tool）
- [manifest.json](issues/12-manifest-json.md) — 不需要独立 manifest；MCP 自带约定，Shell Hook 脚本即清单；关闭
- [Docker 与 GH Actions](issues/14-docker-gh-actions.md) — python:3.11-slim 多阶段构建；tag→wheel→Docker→GHCR amd64起步；volume 挂载 /data；HEALTHCHECK 指向 /health
- [运维脚本](issues/15-ops-scripts.md) — Python 脚本放 scripts/；backup.py>restore.py>upgrade_check.py>reextract.py；分层回填默认 dry-run

## Not yet specified

- **salience 冲突降权**（反义词对碰撞）与现有 contradiction gate 的整合——待后续出票
- **autodream 式 7 天周期蒸馏是否引入**——待后续出票
- **checkpoint 五段会话快照**（aiduMEM `checkpoint.py`）是否移植——待后续出票

## Out of scope

- v13 联邦多 Agent（remembrance 当前单用户单实例，无此需求）
- instinct_graduation（记忆蒸馏成 skill）——与项目定位不符
