# ADR-0064: 直书（Zhishu）白名单高频确定性句型直写引擎与自动规则演化

**日期**: 2026-10-30  
**状态**: Accepted  
**决策者**: 大哥 & Claude  
**来源**: `docs/adr/0013-naming-system.md` 候选意象转正 & `.scratch/v0.43-next-window-2026-11/`

---

## 背景

在兰台长程架构中，`fastpath`（白名单句型直写，`lantai/parsing/fastpath.py`）是一项关键基石：常见确定性句式（如用户自我声明、偏好表达、显式指令）绕过大模型直接安全存入记忆库，做到零 LLM 延迟、零 Token 消耗、且精度 ≥ 95%。
然而，既有 `fastpath` 实现存在以下明显短板：
1. **静态正则硬编码局限**：原 `_PATTERNS` 仅覆盖固定 3 组狭窄句型，对中文口语化强确定性表达（如「以后默认用X」「禁止修改Y」「注意：Z是关键前提」「配置项A设为B」等高价值程序性/约束性事实）全部漏判，被迫兜底走昂贵且可能漂移的 LLM 提取；
2. **缺乏结构化置信度与意图分流**：既有 fastpath 返回结构过于扁平，未与兰台规范事实模型（claims, constraints, methods, actions）做精细化映射；
3. **缺乏规则动态演进与自检反思机制**：系统无法基于高频出现且 100% 确认的事实模式自动拓展直书模式。

---

## 决策

### 1. 传统意象命名转正（ADR-0013）
- 正式中文名：**直书**（Zhishu）。
- 出处：《左传·成公十四年》「董狐，古之良史也，书法不隐」，成语「秉笔直书」——直言直记，不加雕琢，不经润色，不假外求。
- 英文代号：`ZhishuEngine` / `Fastpath`。
- 登记入口：`CONTEXT.md` 词汇表与 `docs/adr/0013-naming-system.md`。

### 2. 直书核心扩展与多轨规则矩阵
升级为 `ZhishuEngine`（直书引擎）：
- **自我事实轨（Fact）**：「我叫X」「我是X」「现居X」「职业是X」；
- **偏好设定轨（Preference）**：「我喜欢X」「我不喜欢X」「偏好X」「习惯X」；
- **程序铁律与负向约束轨（Constraint / Procedural）**：「禁止X」「切勿X」「严禁X」「千万别X」「必须X」「务必X」——直接提取 `constraints`，强制写入 `lane="procedural"`，永不衰减；
- **键值/配置设定轨（Config）**：「配置X为Y」「参数X设为Y」「默认X是Y」；
- **显式备忘轨（General）**：「记住：X」「记一下X」「备忘：X」。

### 3. 直书与提取管线无缝直通
- 提取入口（`extract_memories`）前置调用 `ZhishuEngine.check()`；
- 若命中：输出全规格候选 `ExtractedMemory`，标明 `extractor="zhishu"`，`confidence=1.0`；
- 坚守原则：**宁 miss 不脏写**（未完全吻合高确信特征的语句，绝不贪婪匹配，坚决放行给 LLM 处理）。

### 4. 门面铁律与向后兼容（ADR-0001）
- 保留 `lantai.parsing.fastpath.fastpath_check` 作为旧门面，内部无缝桥接至新 `ZhishuEngine`；
- 既有单元测试与调用端 100% 兼容。

---

## 影响

- 极大降低大模型提取流量开销（高频确定性事实 0 Token 直写）；
- 核心指令与安全铁律秒级纳管，规避大模型幻觉与漏提。
