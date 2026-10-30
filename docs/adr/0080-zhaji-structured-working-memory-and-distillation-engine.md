# ADR-0080: 札记（Zhaji）——工作区认知暂存便签深度演化、即时上下文熔断与双轨同步闭环

**日期**: 2026-11-01  
**状态**: Accepted  
**决策者**: 大哥、Claude  
**来源**: 针对 Working Memory Scratchpad 此前停留在简单覆盖写入、缺乏认知实体契约与结构化分段提炼的架构演进。

---

## 1. 背景与痛点

在智能体长程多轮复杂推理与任务交互中，工作区临时便签（Working Memory）是承载上下文认知精炼的便携载体：
1. **纯扁平字符串覆盖，无法结构化区分思考阶段**：
   既有 `SessionScratchpad` 仅单字段 `content` 覆盖存储，Agent 无法清晰区分当前任务目标（`active_goals`）、关键发现（`key_findings`）与未决疑问（`open_questions`）；
2. **缺乏认知引擎级领域契约与提炼演化能力**：
   缺乏结构化领域实体 `ZhajiItem` 与 `ZhajiDigestResult` 契约，没有独立认知引擎 `ZhajiEngine` 支持确定性/启发式提纯与紧凑化压缩；
3. **容量预算与溢出淘汰机制粗糙**：
   硬编码 1000 字符强行截断，容易在末端截断破坏标点或导致信息不完整，缺少按槽位优先级的平滑预算控制；
4. **命名体系传承与版本对齐**：
   遵循 ADR-0013 命名体系（2–4 字、出自传统意象、名实相副，先登记 `CONTEXT.md` 词汇表再使用）。正名「**札记**」（Zhaji · 古代读书摘记要点之木简便签，取即时留存、随手提炼、去粗存精之意），作为 v0.59.0 版本代号与核心引擎名。

---

## 2. 决策

### 2.1 命名正名与出处
- **中文意象**：**札记（Zhaji）**
- **出处**：古代读书摘记要点之木简便签，《文史通义》读书治学重札记积累。
- **定位**：工作区认知暂存便签深度演化、即时上下文熔断与双轨同步引擎。

### 2.2 核心领域契约
1. **`ZhajiItem` / `ZhajiDigestResult` 契约**：
   ```python
   @dataclass
   class ZhajiItem:
       session_id: str
       active_goals: list[str] = field(default_factory=list)
       key_findings: list[str] = field(default_factory=list)
       open_questions: list[str] = field(default_factory=list)
       notes: list[str] = field(default_factory=list)
       updated_at: datetime = field(default_factory=utcnow)
       user_id: str | None = None
       tenant_id: str | None = None
       fenced_content: str = ""


   @dataclass
   class ZhajiDigestResult:
       session_id: str
       item: ZhajiItem
       total_slots: int
       compact_text: str
       truncated: bool = False
       fenced_content: str = ""
   ```
2. **核心引擎 `ZhajiEngine`**（`lantai/cognition/zhaji.py`）：
   - `distill_text(raw_text: str) -> ZhajiItem`：结构化解析纯文本便签；
   - `digest_session(session_id: str, write: bool = False) -> ZhajiDigestResult`：读取或持久化提纯便签；
   - `format_prompt_context(item: ZhajiItem, max_chars: int = 1000) -> str`：平滑预算控制与樊篱包裹；
   - 贯彻「宁 miss 不脏写」与租户行级 ACL 隔离（`_check_access`）。

---

## 3. 影响

- 统一将工作区便签升级为结构化、多槽位、可提纯的一级认知资产；
- 提供安全的平滑预算控制与樊篱（`<memory_data>`）防护；
- 支撑 MCP 93 工具池扩充与认知上下文 `zhaji_digest` 切面呈现。
