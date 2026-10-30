# ADR-0082: 钩沉（Gouchen）——历史语境回溯与沉埋记忆检索恢复引擎

**日期**: 2026-11-01  
**状态**: Accepted  
**决策者**: 大哥、Claude  
**来源**: 针对长期运行后归档与衰变记忆不可达、缺乏指定历史时态锚点语境无未来污染重构能力的架构演进。

---

## 1. 背景与痛点

随着知识库规模与运行时间的持续扩展，大量记忆进入深层衰减或归档状态：
1. **常规检索无法触达沉没记忆**：
   活跃检索受限于 `status == "active"` 和置信度门槛，已归档（`archived`）或深度弱化（`weakened`）的历史经验沉入底层，缺乏主动打捞通道；
2. **缺乏历史语境切片（HistoricalContext）重建能力**：
   复盘历史决策时，直接拉取当前活跃记忆会引入“事后诸葛亮”的未来知识污染；系统缺乏基于历史事件锚点 $T_{anchor}$ 精准截取当时上下文的能力；
3. **缺乏专门的打捞钩沉领域契约与引擎**：
   缺乏结构化领域实体 `GouchenItem`、`HistoricalContext` 与 `GouchenResult` 契约，没有独立认知引擎 `GouchenEngine` 统一调度与过滤；
4. **命名体系传承与版本对齐**：
   遵循 ADR-0013 命名体系（2–4 字、出自传统意象、名实相副，先登记 `CONTEXT.md` 词汇表再使用）。正名「**钩沉**」（Gouchen · 《易·系辞上》「探赜索隐，钩深致远」，钩取沉埋之物、打捞沉没记忆），作为 v0.61.0 版本代号与核心引擎名。

---

## 2. 决策

### 2.1 命名正名与出处
- **中文意象**：**钩沉（Gouchen）**
- **出处**：《易·系辞上》「探赜索隐，钩深致远」，宋代王应麟《困学纪闻》等考据治学常用“钩沉”指打捞湮没典籍。
- **定位**：历史语境回溯与沉埋记忆检索恢复引擎。

### 2.2 核心领域契约
1. **契约结构**：
   ```python
   @dataclass
   class GouchenItem:
       memory_id: str
       content: str
       sunken_score: float
       decay_score: float
       age_days: float
       status: str
       lifecycle_status: str
       event_time: datetime | None = None
       user_id: str | None = None
       tenant_id: str | None = None
       fenced_content: str = ""


   @dataclass
   class HistoricalContext:
       anchor_time: datetime
       memories: list[GouchenItem] = field(default_factory=list)
       total_count: int = 0
       summary: str = ""
       fenced_content: str = ""


   @dataclass
   class GouchenResult:
       query: str
       total_dredged: int
       items: list[GouchenItem] = field(default_factory=list)
       summary: str = ""
       fenced_content: str = ""
   ```

2. **核心引擎 `GouchenEngine`**（`lantai/cognition/gouchen.py`）：
   - `compute_sunken_score(decay_score, age_days) -> float`：计算沉没度打分；
   - `dredge_sunken_memories(query, min_sunken_score, limit) -> GouchenResult`：穿透状态穿透打捞；
   - `reconstruct_historical_context(anchor_time, limit) -> HistoricalContext`：指定时间锚点的历史无未来污染语境重建；
   - 贯彻「宁 miss 不脏写」与租户行级 ACL 隔离（`_check_access`）。

---

## 3. 影响

- 为智能体赋予主动打捞沉埋事实与精准复盘历史语境的能力；
- 杜绝历史回溯中的未来信息污染；
- 支撑 MCP 95 工具池扩充与认知上下文 `gouchen_digest` 切面呈现。
