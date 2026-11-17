# ADR-0087：运行时、UTC存储与向量库升级

日期：2026-11-17
状态：Accepted（用户授权执行全部已列升级；上线以验证结果为准）

## 背景

生产0.68.1使用Python3.11.15、SQLModel0.0.42、Chroma0.6.3和APScheduler3.11.3。终端路由残留7处Legacy Query，Docker使用已弃用legacy builder。用户明确要求升级所有已列过时项。

## 决策

- 终端主键读取使用Session.get，集合删除使用SQLAlchemy delete/SQLModel.exec；复用已做归属检查的对象，减少重复查询，保持ACL、404、事务与级联契约。
- SQLModel升级0.0.50并采用默认UTCDateTime。应用写入和查询使用aware UTC；历史SQLite的无时区文本已按UTC约定写入，迁移不改墙钟数字。只有明确兼容旧UTC输入的入口调用ensure_aware；不全局monkeypatch、不全表回退NaiveDatetime。
- Chroma升级1.5.9。旧库只能由0.6.3在私有冷副本上读取导出；新环境仅导入全新目录，旧目录不被新Chroma打开。保留所有collection名、IDs、向量、metadata/document/URI和真实索引参数。
- 旧库的collection metadata可与VECTOR segment实际配置不一致。迁移以只读SQLite中VECTOR segment设置为依据（缺省L2），记录declared/actual差异，不凭标签把距离改成cosine。
- 默认要求float32向量精确相等；只有cosine重建的已证明舍入现象可显式接受至多1 ULP，报告差异，不称bit无损。新进程重开后验证全部ID、维度/值、ACL membership、全self与选定top-k距离；等距边界的ID替换明确计数，不将其隐去。本次真实L2库保持0 ULP；显式提高ef_search从100至1000，以float64全量距离oracle验证每次查询新recall不低于旧值，允许新索引找到旧图漏掉的近邻。
- schema45精确规范memorycandidate.role六种已知旧小写表示，实际副本仅118行observation改为OBSERVATION；其它业务列、时间与正文不变。未知值和NULL保留并告警，不作猜测。
- Python目标优先最新稳定3.15.2，但onnxruntime1.29没有cp315发行轮子也无源码发行；采用最高已验证依赖支持稳定版本3.14.10。保留项目>=3.11源码兼容最低声明，生产镜像和主要CI使用3.14。
- Docker主机安装校验官方sha256的buildx0.39.0，使用已有BuildKit0.26.2。镜像多阶段构建，Python3.14.10、uv0.13.8和uv.lock冻结依赖；builder/runtime使用相同/opt/venv路径，禁止构建时自动漂移依赖。构建上下文排除冷备、凭证、scratch、其他worktree和虚拟环境。
- APScheduler3.11.3是查询时最新稳定版，4.x只有alpha。保留已验证3.x内存store与恢复adapter：官方自定义scheduler扩展文档本就依赖_process_jobs；公开API无法等价提供完整循环异常恢复与非阻塞活性快照。不上预发布，也不为消除下划线重写调度架构；升级主版本前重新评审。

## 验证与回滚

真实SQLite的insert/update/query拒naive、偏移归一、旧数据回读、时间窗、CAS与JSON快照回放；真实Chroma CRUD、错误补偿、跨版本冷备导入与新进程核对；全量测试加真实库护栏；Linux同镜像冷副本升级、检索及HTTP生命周期验证。

最终生产切换须停止写入并同时备份SQL/Chroma。新目录导入核对通过后才指向新数据根，旧镜像和旧数据保留。若失败回旧运行时和同一冷备时点数据；新格式不交旧Chroma读取。上线后新增数据需保留后再考虑回滚。无公开tag推送。
