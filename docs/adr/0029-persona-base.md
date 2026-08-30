# ADR-0029: 器识——Persona 人格基座与三层认知模型

**日期**: 2026-08-30
**状态**: Accepted
**决策者**: 大哥
**来源**: 路线图 v0.16.2 观察池选一；借鉴 aiduMEI / MemGPT 人格固定层，构建兰台系统级立身基座。

---

## 背景

兰台记忆系统此前具备两个维度的上下文沉淀：
1. **动态记忆（MemoryItem）**：事实、经验、偏好等碎片知识，受遗忘衰减（Chronos）影响；
2. **会话快照（底本 Checkpoint）**：五段短期会话快照，记录“正在做什么与下一步待办”。

然而，长程对话中缺乏一个恒定、不随时间衰减的**角色人格与最高准则基底**——即 Agent 是谁、秉持何种言语风格（如诗词点缀）、遵循何种行事戒律（如宁 miss 不脏写、核心函数不 mock）、具备何种认知底色（如大哥为尊、本地优先）。

---

## 决策

确立**「器识」（Qishi）** 人格基座体系，采用 L/G/E 三层模型，形成系统级恒定基底：

| 层面 | 核心内涵 | 典型内容 |
|---|---|---|
| **L (Linguistic Style)** | 言语风格层 | 沉稳典雅、名实相副、言简意赅、古诗词点缀思考 |
| **G (Goals & Guidelines)** | 行为戒律层 | 宁 miss 不脏写、核心函数不 mock、人工闸门裁决优先、严格遵守工程纪律 |
| **E (Epistemic Profile)** | 认知底色与事实 | 尊重大哥、华硕天选三硬件环境、本地第一、安全边界明确 |

### 关键机制设计

1. **持久化与激活**：
   - 引入 `PersonaProfile` 表（id, name, is_active, linguistic_style, guidelines, epistemic_facts, created_at, updated_at），Schema 迁移至 v15；
   - 默认提供基线人格「兰台执笔」，支持多 Profile 切换（同一时刻仅一个 `is_active=True`）。
2. **会话首轮注入（与底本协同）**：
   - `persona_service.format_persona_context` 格式化为标准人格基底提示块；
   - 在会话启动注入（`inject_checkpoint_context`）中并行注入，使 Agent 首轮即具备明确风骨。
3. **混合检索加权（Persona Boost）**：
   - `hybrid_search` 在对 `preference` 和 `rule` 分轨记忆打分时，结合 Persona 中声明的准则与风格，给予最高 1.1x 的 Persona Boost 加权。
4. **接口面开放**：
   - REST：`GET /persona/active`, `GET /persona/list`, `POST /persona`, `POST /persona/{id}/activate`
   - MCP：`persona_get`, `persona_set`

---

## 理由

1. **名实相副**：「器识」取自《新唐书·裴行俭传》“士之致远，先器识而后文艺”，器识为格局胸襟与立身品格，高于具体文艺技能，与系统级 Persona 定位完美契合。
2. **恒定不衰减**：Persona 属于系统基础宪章，不受遗忘算法（Chronos）衰减（decay_score 恒 1.0），与易衰减的日常聊天区分开。
3. **架构正交**：Persona 负责角色风骨，底本负责短期会话任务，记忆库负责领域知识，职责边界清晰。

---

## 影响

- 模型：新增 `PersonaProfile` (`lantai/models/tables.py`)，升级数据库迁移至 v15 (`lantai/storage/db.py`)。
- 服务：新增 `lantai/services/persona_service.py`。
- 路由与工具：新增 `lantai/api/routes_persona.py`，注册到 `api_server.py` 与 `scripts/mcp_server.py`。
- 检索与注入：`lantai/services/checkpoint_service.py` 与 `lantai/retrieval/hybrid.py`。
- 测试：`tests/test_persona.py`（全链路真实 SQLite 不 mock 冒烟）。

---

## 相关

- [ADR-0013](0013-naming-system.md) — 器识命名登记
- [ADR-0021](0021-session-checkpoint.md) — 底本会话快照
- [CONTEXT.md](../../CONTEXT.md) — 器识词汇定义
