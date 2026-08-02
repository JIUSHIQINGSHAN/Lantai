# 14 — 运维脚本

**What to build:** 四个 Python 脚本放 `scripts/`，按优先级排序：`backup.py`（备份 SQLite db + ChromaDB dir + .env.example，不含密钥）、`restore.py`（停服 → 覆盖文件 → 重启）、`upgrade_check.py`（检查 schema 迁移 + 向量维度变更 + 配置项新增，输出兼容性报告）、`reextract.py`（从 RawDocument 重新跑 extractor + gate + evolution，默认 dry-run，`--apply` 才执行）。不做 CLI 子命令框架。

**Blocked by:** 13 — Docker + GH Actions

**Status:** ready-for-agent

- [ ] `scripts/backup.py` 备份 SQLite + ChromaDB + .env.example
- [ ] `scripts/restore.py` 停服 → 覆盖 → 重启
- [ ] `scripts/upgrade_check.py` 检查 schema/维度/配置差异
- [ ] `scripts/reextract.py` 从 RawDocument 重跑提取链路，默认 dry-run
- [ ] 前 two 先做，后 two 后续
