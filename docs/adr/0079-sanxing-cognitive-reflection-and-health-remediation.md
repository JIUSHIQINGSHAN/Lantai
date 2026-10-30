# ADR-0079: 三省（Sanxing）——认知自省深度演化、因果违例闭环与健康审计收敛

**日期**: 2026-10-30  
**状态**: Accepted  
**决策者**: 大哥、Claude  
**来源**: 针对健康审视此前仅停留在静态只读诊断、缺乏与认知演化深度联动与自愈闭环的架构演进。

---

## 1. 背景与痛点

在智能体持续摄取记忆与长期反思演化中，系统存量记忆库可能积累隐性陈旧知识与健康缺陷：
1. **反思自省缺乏与认知模式演化及因果违例的主动自愈闭环**：
   既有 `SanxingEngine` 仅支持静态诊断四类缺陷（被取代残留、时效过期、未决冲突、低帮助碎片），无法直接联动 `EvolutionEngine`、`ReflectionEngine` 生成自愈处置提案；
2. **缺乏结构化可审计的自省修复建议与健康演化契约（SanxingRemedyResult）**：
   自检发现问题后无法输出精确的处置动作（如针对陈旧事实的 `supersede_retract`、针对过期条目的 `archive_expire`、针对冲突隐患的 `arbitrate_dispatch`、针对低效碎片的 `demote_deprecate`）；
3. **缺少统一的一级自省自愈服务与 REST/MCP 端点支撑**：
   需在服务层提供一键巡检自愈服务 `sanxing_remedy_service`，挂载端点 `POST /sanxing/remedy`，并在 MCP 扩充至 **92 工具里程碑**（新增 `sanxing_remedy` 自省自愈工具），确保 1:1 双向对齐守卫；
4. **命名体系传承与版本对齐**：
   遵循 ADR-0013 命名体系（2–4 字、出自传统意象、名实相副，先登记 `CONTEXT.md` 词汇表再使用）。正名「**三省**」（Sanxing · 《论语·学而》「吾日三省吾身」，取审视功过、自省自愈之意），作为 v0.58.0 版本代号与核心引擎名。

---

## 2. 决策

### 2.1 命名正名与出处
- **中文意象**：**三省（Sanxing）**
- **出处**：《论语·学而》「吾日三省吾身」。
- **定位**：认知自省深度演化、因果违例闭环与健康审计引擎。

### 2.2 核心领域契约
1. **`SanxingRemedyItem` / `SanxingRemedyResult` 契约**：
   ```python
   @dataclass
   class SanxingRemedyItem:
       target_id: str
       defect_type: str  # superseded_stale | expired | open_conflict | unhelpful
       recommended_action: (
           str  # supersede_retract | archive_expire | arbitrate_dispatch | demote_deprecate
       )
       risk_level: str = "low"  # low | medium | high
       detail: dict[str, Any] = field(default_factory=dict)
       reason: str = ""
       executed: bool = False
       fenced_content: str = ""


   @dataclass
   class SanxingRemedyResult:
       total_defects: int
       remedies: list[SanxingRemedyItem] = field(default_factory=list)
       applied_count: int = 0
       health_score_before: float = 100.0
       health_score_after: float = 100.0
       summary: str = ""
   ```
2. **核心引擎增强 `SanxingEngine`**（`lantai/cognition/sanxing.py`）：
   - `diagnose_health() -> SanxingAuditResult`：存量记忆健康审计纯函数/查询；
   - `generate_remedies(auto_apply: bool = False) -> SanxingRemedyResult`：生成自愈策略并支持一键落库；
   - 贯彻「宁 miss 不脏写」与租户行级 ACL 隔离（`_check_access`）。

---

## 3. 影响

- 统一将记忆健康自检升级为主动自愈与演化闭环；
- 自省处置方案结构化透出，所有自愈动作可解释、可追溯；
- 提示词安全防护全面采用樊篱（`<memory_data>`）隔离；
- 支撑 MCP 92 工具池扩充与认知上下文 `sanxing_digest` 切面呈现。
