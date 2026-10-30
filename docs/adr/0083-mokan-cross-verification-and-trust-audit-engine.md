# ADR-0083: 磨勘（Mokan）——多源认知证据交叉勘验与可信度自证引擎

**日期**: 2026-11-01  
**状态**: Accepted  
**决策者**: 大哥、Claude  
**来源**: 针对多源异构认知记忆缺乏跨实体交叉核验、缺乏系统性可信度自证评估与矛盾自动识别降权机制的架构演进。

---

## 1. 背景与痛点

随着知识库与 Agent 长期交互的不断加深，多来源、多时段注入的事实与经验并存：
1. **单源孤证与幻觉风险**：
   单条记忆仅携带抽取时的静态 confidence，缺乏多源独立证据（Multiple Independent Sources）的交叉印证，难以辨别孤证与偶发错觉；
2. **缺乏系统性可信度评估算法**：
   现有检索打分主要偏重词面与语义相关性，缺乏基于来源独立性、证据支持链数量及矛盾抑制的动态可信度得分（Trust Score）；
3. **冲突与矛盾勘验割裂**：
   知识演化中的矛盾事实若未被及时检出与标记争议（disputed），会在后续上下文中引入混乱；
4. **命名体系传承与版本对齐**：
   遵循 ADR-0013 命名体系（2–4 字、出自传统意象、名实相副，先登记 `CONTEXT.md` 词汇表再使用）。正名「**磨勘**」（Mokan · 《宋史·选举志》「磨勘之法，核验资历功过以定升降」），代表多源认知证据交叉勘验与可信度自证引擎，作为核心引擎名与 v0.62.0 版本代号。

---

## 2. 决策

### 2.1 命名正名与出处
- **中文意象**：**磨勘（Mokan）**
- **出处**：《宋史·选举志》「磨勘之法，核验资历功过以定升降」，宋代设审官院、三班院，对文武官吏之政绩证据、考课文书逐项核实验证以定升迁陟黜。
- **定位**：多源认知证据交叉勘验与可信度自证引擎。

### 2.2 核心领域契约
1. **实体与结果契约**（`lantai/cognition/mokan.py`）：
   ```python
   @dataclass
   class MokanVerificationItem:
       memory_id: str
       content: str
       confidence_score: float
       verification_status: str  # verified / disputed / unverified
       cross_sources: list[str] = field(default_factory=list)
       contradiction_ids: list[str] = field(default_factory=list)
       trust_score: float = 0.5
       evidence_count: int = 1
       user_id: str | None = None
       tenant_id: str | None = None
       fenced_content: str = ""


   @dataclass
   class MokanAuditResult:
       query: str
       total_audited: int
       verified_count: int
       disputed_count: int
       items: list[MokanVerificationItem] = field(default_factory=list)
       summary: str = ""
       fenced_content: str = ""
   ```

2. **核心引擎 `MokanEngine`**（`lantai/cognition/mokan.py`）：
   - `compute_trust_score(base_confidence, evidence_count, contradiction_penalty=0.0) -> float`：
     可信度自证打分算法：综合基础置信度、多源证据数增益（边际递减对数或饱和函数）并扣除矛盾惩罚，截断在 `[0.0, 1.0]` 区间；
   - `cross_verify_memories(memories) -> list[MokanVerificationItem]`：
     对一组记忆进行成对与拓扑交叉核验，查找支持证据边与矛盾对立，标注验证状态并计算自证可信度；
   - `audit_contradictions(query, limit) -> MokanAuditResult`：
     基于检索或特定领域审计存量事实的交叉一致性与未决争议；
   - 行级租户与属主安全隔离（`_check_access` 贯彻「宁漏不越权、宁 miss 不脏写」）；
   - 全链路正文施加樊篱（`wrap_as_data`）防注入围栏。

---

## 3. 影响

- 为智能体提供客观证据交叉勘验能力，显著降低长程对话中的单源幻觉；
- 自动化识别矛盾事实并实施安全降权与争议标记；
- 支撑 MCP 96 工具池扩充与认知上下文 `mokan_digest` 切面深度融合。
