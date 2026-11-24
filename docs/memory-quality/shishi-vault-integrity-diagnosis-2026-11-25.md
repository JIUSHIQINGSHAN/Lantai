# 石室封存验真与写锁诊断

## 范围与环境

- 工作树：`.worktrees/shishi-vault-integrity-20261125`；分支 `feat/shishi-vault-integrity-20261125`，基线 `ad5df575`。
- 仅修改本任务工作树，不提交、不推送、不迁移或重启实际服务。
- 本轮运行元数据为 `model_provider=xingchi`、`model=gpt-6.1-sol`、`effort=max`；路由配置为 Responses 协议，主机 `api.xingchiapi.com`。这是客户端运行与路由记录，不是对上游模型内部实现的独立鉴定。
- 独立测试环境：Python 3.14.10、SQLModel 0.0.50、pytest 9.1.1；测试前清空宿主 `PYTHONPATH`，真实 SQLite 和独立合成数据。

## 确定性复现与根因

`tests/test_shishi_lock_hardening.py` 通过公开引擎、普通核心记忆服务和 REST 入口复现，不替换核心计算或数据库写入。竞争用例只在时间或标识符生成边界插入另一笔真实数据库操作。

| 复现 | 修复前证据 | 根因及修复 |
| --- | --- | --- |
| 普通写覆写不可变封存 | 应抛 `PermissionError` 而未抛 | 普通服务漏检锁；写入口及 SQL 更新条件均拒绝不可变行 |
| 读后并发加不可变锁 | 应拒绝而旧写成功 | Python 检查与 UPDATE 分离；更新语句重验锁，插入冲突回退也走同一检查 |
| 普通写抹除刚安装的可变封存 | 应冲突而未抛，测试真实红灯 | `agent_id` 被旧快照覆盖；普通更新重验封存是否仍与读时一致 |
| 封存快照可原地修改 | 应抛 `FrozenInstanceError` 而未抛 | 数据对象可变；冻结快照 |
| 未封存行算作已验证 | `verified_blocks` 为 1 而预期 0 | 无摘要被默认放行；单列未封存，报告不再声称全部有效 |
| 普通用户重封存损坏行 | 应拒绝而未抛 | 写入不验证原封存；共用旧封存校验，保留异常证据 |
| 会话缓存掩盖其他会话篡改 | `tampered_blocks=[]`，预期 `policy` | ORM identity map 返回陈旧对象；验真与封存读取刷新实体 |
| 同名用户跨租户访问 | 他租户读取非空，普通写未拒绝 | 只比较用户身份；补租户过滤与写入拒绝 |
| 管理员转移归属后的旧写 | 应冲突而未抛，旧属主仍能写入 | 查询后的授权已过期；更新语句重验用户和租户归属 |
| REST 未转换锁拒绝 | 真实路由调用抛异常 | 对明确权限与冲突分别返回 403、409 |

篡改读取过滤、畸形摘要、已知摘要向量、首次插入竞争及管理员恢复另作补充回归，不把加入时已绿的补充用例宣称为先红后绿。

## 兼容性自查

曾尝试全快照比较，既有 F5b 并发版本递增用例报错，因此撤回过宽保护；又以两个普通 agent 的真实并发写重现元数据等值条件的误拦，缩小到封存证据相关条件。普通未封存更新保留末写胜与每次成功写入版本加一，封存状态变化才要求重读重试。

安全策略与限制见 [ADR-0097](../adr/0097-shishi-seal-integrity-hardening.md)。SHA-256 没有独立密钥或外部锚点，不能把数据库任意写权限者的正文、摘要一并伪造或历史回滚说成已解决。

## 验证记录

- 最新聚焦回归：`test_shishi_lock_hardening.py`、石室引擎与 API 冒烟、F5 两条并发契约、核心记忆归属回归，共 **51 passed in 26.77s**；新增安全用例为 31 条。
- 中间版本包含 DSH 契约的聚焦回归为 **55 passed in 73.26s**，不将它冒充最终版本的全量结果。
- `ruff check lantai tests scripts` 通过；`ruff format --check lantai tests scripts` 通过（845 个文件）；`git diff --check` 通过。
- `python scripts/run_forgetting_quality.py --check` 通过：陈旧命中率 0.0，其余五项确定性门槛指标均为 1.0。
- 最终全量验收执行中：任务专属 `verify_full.py` 单独调用 `scripts.release_check.suite_gate_issues(ROOT)`，采用仓库默认 3600 秒时限，真实执行 `pytest tests/ -q --cov=lantai --cov-report=term-missing -o addopts= -p no:cacheprovider -p real_home_tripwire`。额外添加详细用例名、耗时和 XML 报告，不改变用例选择与断言；日志保留在 `.scratch/shishi-vault-integrity-hardening/full-suite.log`。临时目录由现有门禁函数创建和回收。
- 未完成的历史验收不计通过：一次因补充归属竞态修复主动停止；另一次误用较短的 1800 秒时限而超时，清理时遇到子进程文件占用，旧测试进程已退出、对应临时目录已回收。当前默认时限重跑的 exec 会话为 `93759`。

## 自审

- 规范轴：仅本任务工作树演进，无新依赖或表结构变更；公开入口经过真实 SQLite 调用，时钟与标识符包装只控制交错次序；诊断与安全取舍落入本记录和 ADR。
- 规格轴：封存快照冻结，普通写入口与石室入口均受写锁约束，异常封存不冒充有效返回，未封存状态单独报告；保留存量摘要和普通并发版本递增契约。
- 本次为执行者自行审查，未使用其他助手或宣称独立审查。没有提交、推送或发版。
