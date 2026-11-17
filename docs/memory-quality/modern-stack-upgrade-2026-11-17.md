# 兰台技术栈升级验收记录

日期：2026-11-17（北京时间）。用户明确授权执行所有已列升级。

**结果：生产已升级至0.68.2，1420条普通记忆与3块核心摘要保留；945条1024维向量迁入Chroma1.5.9并通过独立新进程完整核对。** 健康检查、调度持续推进、候选工作台及三条业务检索均通过。

## 已部署版本与范围

| 项目 | 升级前 | 实际结果 |
|---|---|---|
| 兰台 | 0.68.1 | 0.68.2 |
| Python | 3.11.15 | **3.14.10** |
| ChromaDB | 0.6.3 | **1.5.9** |
| SQLModel | 0.0.42 | **0.0.50** |
| SQLAlchemy | 2.0.57 | **2.1.9** |
| FastAPI | 0.162.0 | **0.163.1** |
| Pydantic | 2.14.2 | **2.14.3** |
| Pydantic Settings | 2.16.2 | 2.16.3 |
| APScheduler | 3.11.3 | 保持最新稳定3.11.3，不升级4.x alpha |
| Docker构建 | legacy builder | **buildx0.39.0＋BuildKit0.26.2** |
| 旧Query API | terminal中7处 | 全部改为get/select/delete，产品目录扫描0处session.query |
| SQLite schema | 44 | **45** |

Python3.15.2虽已发布，但本次真实依赖安装因onnxruntime1.29没有cp315 wheel且无源码发行失败。采用依赖实际支持、仍处缺陷维护期的3.14.10；不是把3.15预发布装进生产，也没有隐藏兼容限制。

APScheduler稳定最新就是3.11.3。官方自定义scheduler扩展文档仍以`_process_jobs`为实现契约；公开API不能替代已验证的恢复循环、完整循环进展和非阻塞健康快照，因此保留隔离adapter与`<4`约束。此处是经过验证的兼容选择，不宣称已经消除所有私有接口。

## 生产切换与数据保证

- 镜像：`lantai:modern-0.68.2-r2`。
- 固定镜像ID：`sha256:3420a71daa95b775dd6799da7cefcea6890d3d64a74ae782a5d76c27d84f2922`。
- 运行解释器：`/opt/venv/bin/python`；没有继承旧容器PATH/PYTHONHOME/PYTHONPATH。
- 新数据根：`/data/lantai-modern-20261117`挂载到容器`/data`；18767端口与MCP桥连接不变。
- 旧容器：`lantai-before-modern-20261117`，仍使用原`/data/lantai`；原目录逐文件hash保持不变。
- 上线前在停止旧服务后新建同时点SQL/Chroma冷备；所有临时迁移容器禁网，新环境没有打开旧生产Chroma目录。
- SQL逐表比较53表、10173行，全部原行、正文和时间保留。唯一允许变化是118个`memorycandidate.role`从已知小写`observation`规范为`OBSERVATION`。修复前旧0.0.42与新0.0.50均无法ORM读取这些行，属于历史缺陷。
- 新库完整ORM回读通过，schema45，无迁移失败。原1420普通记忆、3核心块均保留。

部署回执见[实际部署摘要](<../../.scratch/stack-upgrade/deploy-summary-20261117T103721.json>)。完整私有日志、原始行快照和配置只保留服务器私有目录；本文不复制记忆正文或凭证。

## 向量迁移验收

本次生产切换时945条向量，保留`remembrance_vectors`集合名。

| 检查 | 结果 |
|---|---|
| IDs、计数、维度 | 945×1024一致 |
| 向量分量 | float32精确相等，**0 ULP** |
| metadata/document/URI | 全部相等 |
| ACL过滤成员 | 18种过滤逐个全量IDs相等 |
| 查询探针 | 999次（含945次自身查询） |
| 自身命中 | 旧945、新945 |
| float64精确距离对照 | 新旧recall@k均1.0，逐查询无退化 |
| 新距离对照最大误差 | `3.4201489063079293e-7`，阈值`1e-5` |
| 索引距离 | 保留旧实际 **L2** |
| 搜索宽度 | 显式`ef_search:100→1000`，记录于导入标记和报告 |

旧库collection标签写cosine，但VECTOR segment实际为空配置、使用默认L2；迁移按实际索引而非标签保留距离语义。先前930冷备和1100条落盘边界库均已独立验证，详见[迁移说明](<../chroma-migration.md>)。

提高搜索宽度可能增加查询计算量，不能把效果只描述成“更快”；本次重点是数据完整及搜索召回不退化。迁移没有调用embedding模型重新生成任何向量。

## 测试与实际服务验收

- 最终全量：**4100 passed、5 skipped、2113 warnings，覆盖率92%，633.08秒**。日志：[full-upgrade3](<../../.scratch/stack-upgrade/full-upgrade3.log>)。
- 真实库护栏已armed，没有命中；全部测试数据隔离。
- SQLModel UTC专项、旧schema37和当前schema44私有副本回放、时间窗/CAS/快照回滚通过。
- Chroma合成和真实库、独立进程持久化、权限过滤、错误补偿验证通过。
- 终端旧查询迁移：29个真实SQLite用例；GET同条记忆查询由3次降为1次，ACL/404/级联事务保留。
- 流式下载成功返回5MiB时，原实现峰值约10.4MiB；BytesIO改造后原7MiB阈值通过。超限有/无Content-Length都实际提前断连，未放大阈值。
- Linux新镜像的930冷备迁移、schema45、完整ORM、HTTP接口和真实调度线程验收通过。
- 生产检索：工具分析四段式、飞书依赖、纠偏原则，3/3命中原有目标记忆。见[生产检索结果](<../../.scratch/stack-upgrade/modern-production-retrieval.json>)。
- 实际控制台：7条知识候选可见，详情可读，证据不足时批准禁用，JS错误0、写操作0。见[生产页面检查](<../../.scratch/stack-upgrade/modern-production-ui.json>)。
- 调度从上线后的02:39:20 UTC推进到02:42:50 UTC，线程存活、健康、连续错误0；观察约5分钟，未宣称长周期业务任务全部重新跑一遍。见[后续状态](<../../.scratch/stack-upgrade/modern-production-final-status.json>)。

大量warnings主要是新Python开始暴露既有测试SQLite连接关闭不完整的ResourceWarning，以及少量枚举序列化等警告；本次没有全局忽略它们，也不将测试通过解释为无维护欠账。首轮与第二轮失败均明确归因修复后重跑，未放宽数据/权限/索引判据。

## 构建与供应链

- buildx0.39.0官方linux-amd64下载SHA256：`5fb31a097304fbf6dd4038649a0decf32c2b9510e4047acd7d68b9d700c46194`，安装前已校验。
- 基础镜像Python3.14.10-slim-bookworm固定digest：`sha256:3fe1a0b6c2d6b2ebb572906cdabeca75aeb558c0c17db5ffefbcf8094871c32b`。
- 仓库Dockerfile支持uv0.13.8＋uv.lock冻结构建。服务器外网下载较慢，实际交付采用同一lock解析的106个Linux运行依赖、逐包SHA256校验轮子进行BuildKit禁网安装；仅jieba由已校验源码构建纯Python wheel。
- 实际wheel SHA256：`47b3d6375375a1d17e0f3ec1189f9c51062e82a5cf6c75f0ca6448f5327c5e14`。部署前逐个wheel成员与镜像安装内容核对，迁移脚本也核对SHA。
- `pip check`在镜像内通过；最后只调整测试工装与文档，没有改变已验镜像产品代码。
- 单独release_check技术项全通过；发布Git检查因独立分支非master而不通过，dirty/suite在该次辅助检查显式跳过。全量和护栏另以CI同口径通过。本次用户授权直接升级部署，未声称公开tag发布门禁全绿，未推tag。

## 备份与回滚

服务器冷备目录：`/var/backups/lantai/upgrade-20261117/deploy-20261117T023648Z-6520b4ea`。

- `cold-data.tar.gz`：本次同时点SQL/Chroma完整冷备，7779219字节。
- SHA256：`cf0c3041b0b5cc319a00c8d37492862f75fa9ddbec96882c8dcbf3fbb305dac9`。
- 原目录`/data/lantai`未修改；旧容器`lantai-before-modern-20261117`已停止保留。
- `vector-export`含原始精确向量与元数据；`verify.stdout.log`为新进程正式迁移验收。
- `sql-before.private.json`、`sql-after.private.json`、`sql-verification.json`留完整数据对账；权限700目录/600文件。
- `runtime.env`、原容器配置等凭证仅服务器保存，不外传。

回滚时先停止新容器并另存上线后的新增数据，然后保留新容器/目录证据，将旧容器改回服务名并启动（仍挂原`/data/lantai`），连续三次真实健康检查成功才算回滚完成。**不能把新Chroma目录交回0.6.3读取**。本次没有触发回滚。

## 尚存边界

- Python3.15受依赖发行轮子限制未部署；APScheduler4为预发布未采用。它们是实测兼容/稳定性选择。
- 最新监控仍有`ingest_background_only`：宿主带session的写入缺失。这属于宿主写入归属链问题，不是本次依赖升级范围。检索回测时曾短暂触发p95高延迟，后续窗口该项已消退；不能据此保证外部模型延迟。
- Windows迁移CLI要求工作/目标目录ASCII，避免旧/新native HNSW在超过落盘阈值时对非ASCII路径处理失败；Linux生产路径满足。产品存储路径没有新增一刀切限制。
- 原主工作区用户未提交改动保持原样，所有代码在独立升级分支。公开发布/合并不是本轮自动操作。
