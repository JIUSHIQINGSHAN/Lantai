# v0.7 — 树状图谱 + 技能结晶（借鉴 aiduMEI TreeMemory / SkillCrystallizer 窄版）

来源审阅：2026-08-12 aiduMEI v18.3.0 `ducky/tree_memory.py` + `ducky/skill_crystallizer.py`。
原则：**借鉴设计思想，不照搬代码**；存储层保持 SQLite 自研；核心函数必须有不
mock 冒烟测试；「宁 miss 不脏写」——树挂载校验失败不落库、结晶只产候选项人工审核落地。

## 作者实现要点（审阅结论）

- TreeMemory（v17.0，借鉴 Mímir）：`memory_nodes` 父子表 + `node_path` 唯一路径 +
  depth 前缀查询 + 每节点挂载 fact 数（category 精确匹配，v17 修复 LIKE 误匹配）。
- SkillCrystallizer（v17.0，借鉴 Mímir/MemOS）：按 category 分组 COUNT>=3 才结晶，
  排除噪声分类（general/uncategorized/emotion/session/temp/draft）；procedure 只记
  fact_key 摘要不塞全文；**Mímir 铁律：LLM 只能建议不能直接 commit**——结晶产物是
  候选项（candidate），人工审核后才 approved 落地。

## 兰台窄版差异（不照搬）

- 无 facts 表：挂载统计走 `memoryitem.tree_path` 显式挂载（前缀 LIKE 统计），不靠
  category 名字匹配（避免作者 v17 之前的误匹配坑）。
- 结晶聚类复用既有 `autodream.cluster_memories`（同 lane + 共享关键词，确定性）；
  噪声 lane 排除 general/chat；approved 落成既有 Skill 资产（memory_type=skill，
  复用 create_skill，需人工提供 steps——宁 miss 不脏写）。

## 票据

- 01-tree-memory：记忆分类树（MemoryNode + memoryitem.tree_path v9 迁移 + 服务/REST/MCP）
- 02-skill-crystal：技能结晶（SkillCrystal 候选项 + detect/decide + 服务/REST/MCP）

## 明确不吸收

- 作者 `facts` 表耦合与 category 名字匹配挂载（脆）
- 自动 approved 结晶（无人工审核即落地，违反既有闸门哲学）
- 多 Agent 联邦树（federation 属万神殿赛道，已明确不追）
