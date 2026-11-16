# ADR-0086：调度存活与普通候选审核闭环

日期：2026-11-17
状态：Accepted（用户授权自主修复，实施与生产验收记录另附）

## 事实与问题

生产0.66在2026-11-05因SQLAlchemyJobStore更新任务时SQLite锁异常导致APScheduler线程退出，而running标志与HTTP健康继续报正常。现有任务全部由启动代码重注册、任务完成戳另存scheduler_run，持久任务表没有提供跨启动保存用户计划的契约。

普通MemoryItem candidate与MemoryCandidate.pending_review是不同表。前者未进入待办，其lifecycle_status默认active又让生命周期检查错判；直接promote/transition只改状态而不建索引、留快照。生产历史477条候选有9个角色正文组，另建add提案曾生成3条已索引活跃记忆而未收口原候选。

## 决策

1. 运行时计划使用MemoryJobStore。计划真源仍是配置与start_scheduler代码，完成时间继续持久化scheduler_run；每日补跑保留。旧apscheduler_jobs表保留，不删除、不加载；停机期间不承诺逐个回放interval任务。
2. 调度循环捕获可恢复异常并有限指数退避，保留日志与失败状态；线程存活与完整循环新鲜度作为健康依据，不能用单一running标志。健康探针不能等待任务存储锁。配置禁用调度不视作故障。
3. 新增工作项kind=knowledge_candidate，显式区分摄取候选。列出普通候选、来源、已有Evidence以及审核阻断原因。
4. 新增POST /knowledge-candidates/{id}/review。审批需非空理由、expected_version、request_id与显式evidence_ids。证据必须存在、有效、关联候选/来源且在权限内；不接受客户端分数，不为旧条目自动伪造证据。approve创建新活跃semantic记忆，保留认知角色和来源，原候选consolidated并指向结果；reject归档保留原文。无版本批量审批不提供。
5. 审核成功包含FTS、向量核验、版本与checkpoint；失败不得返回成功。请求回执幂等且同键异文拒绝。候选、归档、撤回和已整合条目不得通过通用生命周期转active绕开专职恢复/审核。
6. 生命周期读/巡检优先尊重存储status：candidate始终按候选观察；不因默认lifecycle_status active自动衰减、复元或晋升。
7. 存量治理为显式离线维护：按归属、角色、正文分组；同文折叠不物理删除，前后快照与来源关系保留。已转化组可在核对结果ID、正文指纹与来源链后关联到已有活跃结果；其余保留一条待审代表。计划先预演并校验哈希，提交前重新比对版本，整个SQL事务生效；生产冷备支持恢复。

## 约束与取舍

- 不添加新中文正式功能名，沿用既有候选、案牍与知命术语；knowledge_candidate为区分表语义的技术字段。
- 新审核API拒绝证据不足属于显式可见待审，不能用高置信分数替代验证。
- 无外部前端依赖，原生dialog和同源ES模块；只读来源文本用textContent。
- 没有跨SQL/向量分布式事务：需在SQL提交前完成可验证向量写入并在失败时补偿，重试幂等；残余失败要明确报告。
- 旧状态/新schema生产切换前备份；容器镜像与原配置保留便于回退。不自动推送公开tag。

## 验证

真实调度线程＋临时SQLite争用，启停/死亡/持续不进展健康测试；真实临时SQLite/FTS/Chroma审核主路径、失败补偿、越权、幂等、陈旧版本；真实Chromium审核取消与确认请求边界；生产副本迁移回放；部署后持续观察next_run推进、worker完成和定向检索。
