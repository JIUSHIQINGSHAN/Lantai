# 兰台记忆（Lantai）— 上下文

## 项目定位

AI Agent 长期记忆管理系统——摄取、闸门、演化、检索、遗忘的完整链路。

## 词汇表

| 术语 | 定义 |
|------|------|
| **兰台（Lantai）** | 项目中文名。取自汉代皇家档案馆「兰台」——为 AI 保存、检索、演化、遗忘长期记忆的档案库。英文代号 Lantai |
| **锦囊（Jinnang）** | `pending_review` 待审候选队列的别名，取自「锦囊妙计」——待拆的锦囊交用户裁决 |
| **lane**（分轨） | 记忆类型分轨：fact / rule / experience / preference / chat / general。每轨有独立的衰减参数和检索权重 |
| **tier**（层级） | 记忆层级：working（工作记忆） / long_term（长期记忆）。working 超过 TTL 且无帮助时归档 |
| **gate**（闸门） | 记忆准入控制：置信度阈值 + 新颖度评分 + 矛盾检测 → 五档决策（reject / working_only / promote_semantic / promote_procedural / archive_conflict） |
| **coalesce**（潮波合并） | 短消息异步缓冲合并，减少 LLM 提取调用次数。缓冲键 = `user_id + lane`，按 lane 分档定义冲刷参数（`LANE_COALESCE_PROFILES`）。`/add` 开关切换（`COALESCE_ENABLED`），一个入口自动分流。见 [ADR-0003](docs/adr/0003-coalesce-buffer-key.md) |
| **fastpath**（白名单直写） | 特定句型绕过 LLM 提取直接写入，原则「宁 miss 不脏写」。三类句型：自我声明/偏好表达/显式指令。正则匹配放 `parsing/fastpath.py`。命中直接返回 `fastpath_candidate`，不入缓冲 |
| **candidate**（候选记忆） | 从 RawDocument 经 LLM 提取的结构化知识，尚未通过闸门。状态值：`new`（LLM 提取）/ `fastpath`（白名单直写）/ `rejected`。去重（余弦相似度）在 candidate 创建时、gate 之前执行 |
| **proposal**（提案） | 候选记忆通过闸门后生成的变更提案（add/update/merge/deprecate），待应用或拒绝 |
| **checkpoint**（检查点） | 记忆变更前后的快照，用于回滚 |
| **decay_score**（衰减分） | 记忆保持强度，按 lane profile 指数衰减。降到极低时自动转 archived |
| **facade rule**（门面铁律） | 重构约束：只搬家不改语义，旧 import 全绿。见 [ADR-0001](docs/adr/0001-facade-rule.md) |
| **service layer**（service 层） | 路由 handler 下沉的业务逻辑层。handler 只做 HTTP 解析/返回，业务逻辑在 service |
| **archived**（归档记忆） | decay_score 极低后自动转换的记忆状态，不参与检索（`WHERE status='active'`），但物理不删 |
| **Shell Hook** | 零依赖 CLI 注入路径：stdin 收 JSON，stdout 返回 Markdown 上下文。2s 超时返回空。见 [ADR-0006](docs/adr/0006-shell-hook-contract.md) |
| **search_trace** | `/search?trace=true` 返回的每步诊断数组：`{step, elapsed_ms, candidate_count, score_range}`。overhead < 1ms |
| **water_level**（水位） | coalesce 缓冲水位指标（active_keys + total_messages），由 `/stats` 暴露，用于监控写入节流状态 |
| **verbatim**（原文直存） | `memory_type="verbatim"` 的记忆：内容零 LLM 直入 FTS5+向量（`POST /add/raw`），内容 sha256 作 key 幂等去重，不走提取/闸门/演化。见 [ADR-0009](docs/adr/0009-raw-drawer-verbatim.md) |
| **conflict_event**（冲突账本） | 冲突消解确定性层（规则命中）的审计账本：memory_id / rule_name / detail / status（open→resolved/dismissed），人工裁决不改记忆状态。见 [ADR-0010](docs/adr/0010-conflict-resolution-layer.md) |
| **Skill 资产**（可注入技能） | `structure.steps` 非空的 procedural 记忆：以 `## Skill: 名称` + 描述 + 编号步骤注入上下文，Agent 可照步骤执行。沉淀链路 proposer → promoter（提案落库带 structure），见 [ADR-0011](docs/adr/0011-skill-asset.md) |
| **scene**（场景聚合） | 一组相关记忆的导航实体（`MemoryScene` 表）：embedding 聚类构建，检索命中时导航块优先注入（`## Scene: 名称` + 摘要 + 成员 key），详情用 `scene_get` 下钻——渐进式披露。heat = 成员 `use_count` 求和。见 [ADR-0012](docs/adr/0012-scene-layer.md) |
| **provenance**（提取来源） | 记忆的出生证明：`{prompt, model, extracted_at}`，从候选（提取时）经提案（继承）到 MemoryItem（落库）全程同源，回答"这套记忆是谁产出的"；prompt 名即版本（extract-v1 / fastpath-direct / dialogue-fastpath / dialogue-chitchat）。见 [ADR-0015](docs/adr/0015-provenance.md) |
| **ACL**（访问收窄） | 按 agent_id 绑定 lane 集：`AGENT_LANE_BINDINGS` 配置后，绑定 agent 只能检索/写入自己 lane 集内的记忆（`X-Agent-Id` header，缺失/未绑定 403，检索结果宁 miss 不放行）；空配置 = 不启用。见 [ADR-0013](docs/adr/0013-naming-system.md) 命名登记 |
| **冷启动导入**（冷启动导入） | 历史数据一次性导入双通道：① verbatim 直存——JSONL 逐行原文零 LLM 落库，created_at/updated_at 保留原始时间戳，内容 sha256 幂等去重，`POST /import/jsonl` + `scripts/import_jsonl.py`；② 对话链导入——L0 会话 JSONL（{role, content, timestamp}）喂既有摄取链，时间戳经 provenance 继承到记忆，`scripts/run_import.py --dry-run` 预览。非法行记报告不静默修正（宁 miss 不脏写） |
| **vault**（档案） | 记忆档案只读浏览：`GET /memories` 分页 + lane/status/decay_class 过滤，`/ui/vault` 控制台同时展示锦囊待审队列与衰减概览——「存了什么、待裁什么」一眼可见。见 [ADR-0013](docs/adr/0013-naming-system.md) 命名登记 |
| **offload**（上下文卸载） | 超长记忆全文落 `docs/memory-offload/{memory_id}.md`，Shell Hook 上下文只注入摘要 + 路径，需要时经 MCP `offload_read` 取回全文——上下文不随单条记忆长度增长。见 [ADR-0016](docs/adr/0016-offload.md) |
| **记忆 Wiki**（持续维护知识库） | 场景/技能 → `docs/memory-wiki/` 页面 + `index.md`（先看目录）+ `overview.md` 综述（LLM 优先，失败确定性兜底），`[[wikilink]]` 下钻经 MCP `wiki_read`；`mem_sync` 三件套（scene+digest+wiki）刷新。见 [ADR-0017](docs/adr/0017-wiki.md) |
| **mem: 命令**（命令式维护） | MCP 命令式维护工具：`mem_help`（帮助）/ `mem_sync`（scene 增量聚类补跑 + 今日 digest 重算）/ `mem_create_skill`（结构化沉淀 Skill 资产，procedural 永不衰减）。Agent 显式触发，不依赖自动流程时机。见 [ADR-0014](docs/adr/0014-mem-command.md) |
| **digest**（每日盘点） | 每日清晨生成 `docs/memory-digest/YYYY-MM-DD.md`：新增/修改/总量/待审/归档/检索五项统计，Hermes 经 MCP `get_digest` 或 `GET /digest/today` 读取。见 [docs/daily-digest.md](docs/daily-digest.md) |
| **命名体系** | 中文命名的方向与规则：按对象层级（项目/子系统/机制/数据/版本代号）从传统意象取材，2–4 字、有出处、先登记后使用；见 [ADR-0013](docs/adr/0013-naming-system.md) |
