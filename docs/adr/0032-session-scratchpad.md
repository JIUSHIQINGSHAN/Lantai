# ADR-0032: 札记——Agent 主动式工作区暂存夹与即时上下文机制

**日期**: 2026-08-30
**状态**: Accepted
**决策者**: 大哥
**来源**: 借鉴 Letta / MemGPT 虚拟内存与工作区（Scratchpad）机制；解决长程复杂对话与代码调试中 Agent 缺乏实时主动便签的痛点。

---

## 背景

兰台记忆系统的底本（Session Checkpoint）由系统在会话闭合或压缩时被动落库。但在长程多轮复杂对话（如深度排查 Bug、多步骤重构）中：
1. Agent 缺乏**在对话交互中实时主动记要点**的工具；
2. 缺乏针对当前会话活跃工作区（Working Memory）的实时精炼便签。

---

## 决策

引入**「札记」（Zhaji）** 核心工作区暂存夹机制：

### 核心设计

1. **数据模型 (`SessionScratchpad`)**：
   - 包含 `session_id`, `content`, `updated_at`；
   - 数据库增量迁移升级为 `v16`；
   - 严格限制 `content` 最大 1000 字符，超长自动截断（宁 miss 不脏写）。
2. **首轮 Prompt 协同注入**：
   - 联动 `checkpoint_service.py::inject_checkpoint_context`；
   - 在会话启动时，将 `【札记】` 块与 `【底本】` 块、`【器识】` 人格基座协同拼合注入 System Prompt。
3. **接口与 MCP 工具暴露**：
   - REST：`GET /scratchpad/{session_id}`, `POST /scratchpad/{session_id}`
   - MCP：`scratchpad_get`, `scratchpad_write`

---

## 理由

1. **名实相副**：「札记」出自古代读书或随手摘记要点之木简小帖，与 Working Memory Scratchpad 的定位完全相符。
2. **Agent 主动性突破**：打破传统 Agent 只能被动读取记忆的限制，赋予 Agent 实时自我整理与主导工作区上下文的能力。

---

## 影响

- 存储：升级 `CURRENT_SCHEMA_VERSION = 16`，新增 `SessionScratchpad` SQLModel。
- 服务：新增 `lantai/services/scratchpad_service.py`，更新 `checkpoint_service.py`。
- 路由与工具：新增 `lantai/api/routes_scratchpad.py`，新增 MCP `scratchpad_get` / `scratchpad_write`。
- 测试：`tests/test_scratchpad.py`（真实 SQLite 数据库不 mock 冒烟）。

---

## 相关

- [ADR-0013](0013-naming-system.md) — 札记命名登记
- [ADR-0021](0021-session-checkpoint.md) — 底本会话快照
- [CONTEXT.md](../../CONTEXT.md) — 札记词汇定义
