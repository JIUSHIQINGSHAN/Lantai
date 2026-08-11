# v0.6 — aiduMEI 吸收（借鉴 v18.x 优点）

来源审阅：2026-08-10/11 aiduMEI（原 aiduMEM）v18.0–v18.3 全量源码审阅。
原则：**借鉴设计思想，不照搬代码**（延续 v0.3 移植纪律）；存储层保持
SQLite+FTS5+ChromaDB 自研；所有核心函数必须有不 mock 冒烟测试。

## 票据

- 01-schema-versioned-migrations：PRAGMA user_version 版本化增量迁移
- 02-obsidian-verbatim-ingest：原文直存通道 + Obsidian 双链同步
- 03-fullrun-scheduler-pollution：全量顺序污染排查（测试进程内调度器线程）
- 04-recall-panel：追忆漏斗控制台（RECALL 面板）
- 05-evolve-panel：检索质量看板（EVOLVE 面板）
- 06-vault-panel：档案与锦囊控制台（VAULT 面板）
- 07-import-jsonl：冷启动导入（历史会话 JSONL 批量原文直存，保留原始时间戳）
- 08-acl：资产绑定 + lane 级 ACL（按 agent_id 绑定 lane 集）

## 明确不吸收

- 装饰性登录门禁（作者默认密码 123456 且 API 无鉴权）；本系统沿用 X-API-Key 全路由鉴权
- EvolveMem 自动高频提权（回声室启发式）；只保留显式人工反馈
- 无测试的新特性照搬；凡移植必须补不 mock 冒烟测试
- Code Graph / IDE 钩子等与记忆系统正交的功能
