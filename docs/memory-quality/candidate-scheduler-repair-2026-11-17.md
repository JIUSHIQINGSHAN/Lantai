# 生产调度与候选知识修复记录

日期：2026-11-17；时间均为北京时间（UTC+8）。用户明确授权自主修复。

**结果：生产已部署0.68.1。调度线程持续推进约1小时，全部worker逾期告警清零；477条历史候选已可回滚整理为5条待核实代表和472条折叠记录；3条已存在的派生知识全部通过生产检索回测。**

## 实际部署与验证

| 项目 | 结果 |
|---|---|
| 上线时间 | 06:16:02，新容器启动 |
| 实例 | 原服务器`lantai`容器、原18767端口，未改变MCP桥连接 |
| 版本 | 0.66.0 → 0.68.1 |
| 镜像 | `lantai:repair-0.68.1-20261117` |
| 镜像ID | `sha256:855ab3fd440374c094f67d0c67f55629b8e828109e82c7f1983bc8c787938227` |
| SQLModel | 原0.0.47 → 项目已锁定0.0.42 |
| 数据库迁移 | schema37 → 44，迁移失败0，integrity_check=ok |
| 全量测试 | **4020 passed、5 skipped、13 warnings，覆盖率92%，892秒** |
| 真实库绊线 | 已armed，全程无hit |
| 浏览器 | Chromium157，真实DOM/请求/键盘/非安全HTTP/窄屏验证；实际生产控制台只读验收通过 |
| 07:15复查 | 运行约59分钟；scheduler healthy、thread_alive、consecutive_errors=0；监控alert_count=0 |
| 生产检索 | 工具分析四段式、飞书依赖、纠偏原则：3/3命中预期已存在记忆ID |

证据保存在隔离工作树的[完整回归日志](<../../.scratch/memory-repair/full-round2.log>)、[生产最终状态](<../../.scratch/memory-repair/production-final.json>)、[检索回测](<../../.scratch/memory-repair/production-retrieval.json>)和[真实页面验收](<../../.scratch/memory-repair/production-ui-verification.json>)。

## 修复内容

1. **调度线程存活。** 所有任务计划原本在启动时重建，现改用内存jobstore，避开业务SQLite锁竞争造成“任务已分发、计划更新失败”的边界；运行完成戳仍持久化。循环异常保留日志并有限退避；探针检查真实线程和完整循环进展，失效返回503，读取探针不等待jobstore锁。APScheduler约束`>=3.10,<4`。
2. **知识候选治理。** 普通MemoryItem候选进入独立`knowledge_candidate`待办。详情显示来源、有效Evidence和审批阻断；单条审核要求理由、版本、请求ID和明确选定证据。成功另建长期semantic知识、更新原候选状态、保存checkpoint和来源、同步并验证全文/向量索引。同请求幂等，提交/索引失败补偿并明示。
3. **关闭状态绕行。** lifecycle读面优先尊重status，候选不会被误报active；通用promote/transition和原候选的归档/折叠/撤回恢复不能绕过审核。已驳回或未经审核的候选不能成为下游有效证据；正式知识的正常恢复保留。
4. **控制台闭环。** 摄取候选与知识候选分开；证据可显式选择，质量不足禁批；理由必填；取消/Escape零提交、在途去重、断连后原请求重试并显示持久回执；不支持无版本的知识候选批量批准/拒绝。
5. **上线全量门禁修复。** MCP stderr UTF-8；评测启动器移除机器路径/固定key回退，显式透传受保护生产目录并拒绝重叠，回填导入无执行副作用；维护脚本不再依赖当前目录导入。真实测试保证未把失败改成跳过。

实现决策见[ADR-0086](<../adr/0086-scheduler-liveness-and-knowledge-review.md>)。

## 历史477条处理结果

- 以归属、角色、正文等字段精确分组，共9组；保留全部原文和所有记录ID。
- 468条同组重复项折叠；4条代表经核对提案、结果正文指纹和来源链后关联到3条已有活跃知识。
- 剩余5条代表保持candidate，在工作台显示证据不足，不自动批准：技能安装、Clash配置、飞书旧修复、模型资源偏好的BELIEF与RULE。
- 全部477条有前后checkpoint，版本递增；折叠关系有归属与supersedes边。原位没有删除正文。
- 生产整理事务提交时全库仍是1403条：active419、archived54、candidate5、consolidated925。

后台恢复后发生正常增量：反思补跑新增2条candidate，另有15条verbatim原文直存记录进入active（没有候选来源链，不能归因于本次演化或算作候选转知识成果）；截至最终检查共 **1420条**（active434、archived54、candidate7、consolidated925）。原1403条ID全部存在，正文逐条比较无变化；核心摘要仍为3块。

当前待审：**7条知识候选、12条提案**。其中10条提案来自恢复后显式补跑的autodream，均pending、没有自动应用。新增候选含旧升学背景等需要核实的内容，保持待审，不把恢复生成误称为正确知识。

[最终数据库完整性证据](<../../.scratch/memory-repair/production-integrity-final.json>)包含原记录核对、477条checkpoint计数和新增记录来源元数据。

## 维护补跑

- 反思：启动每日补跑，06:18完成，有scheduled成功审计。
- candidate_ttl、ingest：核实无积压后补跑成功，TTL归档0。
- forgetting：先在生产副本预演，归档变更0、正文变化0，再实际补跑成功。
- param_advice：补跑成功。
- evolve：一次手动HTTP等待超时，未重试重复启动；后续完成戳与定时执行确认06:46成功，非把超时当成功。
- autodream：17簇/17计划，新增10条待审提案，不应用。
- 07:15全部worker均status=ok、overdue=false；调度线程始终保持活性。

## 备份与回滚

服务器备份目录：`/var/backups/lantai/repair-20261117`。

- `cold-data.tar.gz`：停服后SQLite、Chroma和数据目录完整冷备。
- 冷备SHA-256：`a511cf972798a1ed18ce16def47215d8e8ec99503559dd2567d818731547e408`。
- `container-before.json`、`runtime.env`：原配置与凭证仅保留服务器0600文件，不写入报告正文。
- `production-reconciliation-plan.json`、`production-reconciliation-receipt.json`：实际计划和477条处理回执。
- 原容器保留为`lantai-before-20261117`，原镜像未删除。

**回滚必须同时恢复旧镜像与schema37冷备；不能直接用旧0.66读取升级后的库。** 回滚前先停新容器并另存当前数据，保留上线后新增17条等增量；核对备份hash与安全路径，将冷备恢复到新目录，校验SQLite完整性并设置appuser(1001)权限，再替换`/data/lantai`挂载目录、切回原容器。不得覆盖上线后的增量备份。自动部署脚本包含失败回退分支，本次未触发回滚。

## 验证边界与异常处理记录

- 首轮全量21失败、3996通过；已定位并修复后第二轮4020通过。不是跳过红测。
- 单独release_check的技术项通过；Git发布分支检查失败是当前隔离修复分支非master，dirty和suite在该次辅助检查中显式跳过。全量+绊线另行按CI同口径真实执行通过。本次是用户授权的直接修复部署，**未声称公开发布门禁全绿，未推tag**。
- 早期镜像编排脚本因嵌套字符串边界错误误调用本机初始化并失败；已立即修复并加入本地数据库拒绝护栏。核对本机库仍schema44、221条、核心1块，远程未受影响。之后staging全部在服务器独立副本和禁网容器中执行。
- 生产UI初次验收脚本因CSP禁止eval失败，改用真实locator断言；后又因反思新生成2条候选使固定5条断言失败，核实新增时间与来源后改为验证原5代表存在、全部当前待审可见，没有改产品放宽。
- 浏览器仅验证Chromium，未声明其他引擎/物理手机/读屏通过。SQL与Chroma仍无分布式原子提交；已验证补偿和回执，进程硬杀后孤立向量仍以SQL状态过滤兜底。
- 主工作区原有用户未提交改动保持原样；代码在独立修复分支，不覆盖原分支。
