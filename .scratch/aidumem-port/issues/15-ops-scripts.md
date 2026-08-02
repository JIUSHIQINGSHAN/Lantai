# 运维脚本清单：升级检查 / 备份恢复 / 分层回填

Type: grilling
Status: resolved
Blocked by: —

## Answer

五项决议（grilling 2026-08-02 与用户确认）：

### 1. 升级检查脚本

检查 schema 迁移（SQLModel metadata diff）+ 向量维度变更 + 配置项新增。Python 脚本，输出兼容性报告。

### 2. 备份恢复

备份 SQLite db + ChromaDB dir + .env.example（不含密钥）。恢复 = 停服 → 覆盖文件 → 重启。不做 PITR（单用户不需要）。

### 3. 分层回填

从 RawDocument 重新跑 extractor + gate + evolution。默认 dry-run，`--apply` 才真正执行。不走 checkpoint 回滚（那是不同场景）。

### 4. 脚本形态 → Python 脚本

放 `scripts/`。不做 CLI 子命令（需要引入 click/typer，当前不需要）。

### 5. 脚本列表 → 优先级排序

1. `backup.py` — 备份
2. `restore.py` — 恢复
3. `upgrade_check.py` — 升级检查
4. `reextract.py` — 分层回填

前两个先做，后两个后续。

aiduMEM 有运维脚本：升级检查（版本兼容性）、备份恢复（SQLite + 向量存储）、分层回填（从历史数据重新提取记忆）。remembrance 当前只有 `scripts/init_db.py`。

需要决定：

1. **升级检查脚本**：检查什么？schema 迁移？向量维度变更？数据兼容性？
2. **备份恢复**：备份哪些（SQLite db + ChromaDB dir + .env）？恢复流程？是否需要 PITR？
3. **分层回填**：从 RawDocument 重新跑 extractor？还是从 MemoryCheckpoint 回滚？默认 dry-run？
4. **脚本形态**：Python 脚本？Shell 脚本？还是 CLI 子命令？
5. **脚本列表**：最终需要哪些脚本？优先级？
