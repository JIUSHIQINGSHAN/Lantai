# ADR-0034: 辨域——User-Session-Agent 三维硬隔离与偏好域分治

**日期**: 2026-08-30
**状态**: Accepted
**决策者**: 大哥
**来源**: 借鉴 Mem0 多维隔离架构；解决多智能体、多会话并发时全局用户偏好与临时会话上下文互相污染的痛点。

---

## 背景

兰台原有的分轨（`lane`: preference/fact/rule/experience）主要区分记忆性质。然而在长程多 Agent、多会话场景下：
1. 某个特定调试会话的临时参数容易被全局检索当成通用事实；
2. 缺乏由 **User（用户）**、**Session（会话）**、**Agent（智能体）** 构成的三维硬边界。

---

## 决策

引入**「辨域」（Bianyu）** 三维隔离体系：

### 核心机制

1. **三维域定义 (`domain`)**：
   - `user`: 针对用户的稳定画像、个人喜好、硬件环境等长期记忆；
   - `session`: 针对当前对话/特定任务的即时上下文与短期经验；
   - `agent`: 针对智能体系统角色、戒律准则（Guidelines）与行为范式。
2. **自适应 Lane-Domain 映射（宁 miss 不脏写）**：
   - 写入未显式指定 `domain` 时：
     - `preference` / `fact` -> `user`
     - `rule` / `meta` -> `agent`
     - `experience` -> `session`
3. **检索域精细化控制**：
   - `hybrid_search` 引入 `domain` 参数：
     - `domain="user"`: 仅召回用户长期偏好与事实；
     - `domain="session"`: 仅召回当前会话短期经验；
     - `domain="agent"`: 仅召回系统戒律与规范；
     - `domain=None` 或 `"all"`: 全域融合召回。
4. **存储与迁移**：
   - `MemoryItem` 增加 `domain: str = Field(default="user", index=True)`；
   - 数据库增量迁移升级为 `v17`。

---

## 理由

1. **名实相副**：「辨域」出自《周礼·春官·宗伯》“以辨天地四时之域”，取清晰辨别领域边界之意。
2. **多租户与多会话安全**：从数据模型层面彻底杜绝会话间相互污染，使 Agent 既能享受全局用户偏好，又能保持干净独立的会话沙箱。

---

## 影响

- 存储：升级 `CURRENT_SCHEMA_VERSION = 17`，`memoryitem` 表新增 `domain` 字段与索引。
- 检索：`lantai/retrieval/hybrid.py` 增加 `domain` 参数过滤。
- 路由与工具：更新 `routes_search.py`、`routes_memory.py` 以及 MCP 工具。
- 测试：`tests/test_domain_isolation.py`（真实不 mock 冒烟单测）。

---

## 相关

- [ADR-0013](0013-naming-system.md) — 辨域命名登记
- [CONTEXT.md](../../CONTEXT.md) — 辨域词汇定义
