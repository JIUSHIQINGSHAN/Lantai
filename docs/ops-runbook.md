# 兰台运维手册

## 常见问题

### 1. 反思观察期门禁卡住

**症状**：`reflect_observation_status.py --check` 返回 PENDING

**原因**：服务未整夜运行，APScheduler 定时任务未触发。

**检查**：
```powershell
python scripts/reflect_observation_status.py
```
输出示例：`窗口内合格定时运行天数：4/7（14 天窗口）`——需要 7 天中至少 3 天有定时运行。

**解决**：
- 确保 API 服务器持续运行：`python api_server.py`（全天）
- 或使用 Windows 计划任务每晚自动启动
- 察窗口径（ADR-0027）已从"连续"改为"窗口内计数"，容忍间歇性缺失

### 2. 候选管道堆积

**症状**：`python scripts/memory_overview.py` 显示大量待审候选，但记忆总量无增长

**原因**：闲聊与低置信度提取冲击候选队列，TTL 7 天自动归档（净损失）。

**检查**：
```powershell
python -c "
import sys; sys.path.insert(0, '.')
from lantai.core.settings import settings
import sqlite3
db = settings.DATABASE_URL.replace('sqlite:///', '')
c = sqlite3.connect(db); c.row_factory = sqlite3.Row
for r in c.execute('select status, count(*) n from memorycandidate group by status'):
    print(r['status'], r['n'])
for r in c.execute('select cast(extractor_confidence as int) b, count(*) n from memorycandidate where status=\"pending_review\" group by b'):
    print('conf bucket', r['b'], r['n'])
"
```

**解决**：
- 沙汰（ADR-0026）已修复：闲聊直接 rejected，不再排队
- 若仍有大量低置信度候选，可调高 `CANDIDATE_MIN_CONFIDENCE`（当前 0.0，建议 0.15）
- 在 `.env` 中设置 `CANDIDATE_MIN_CONFIDENCE=0.15`

### 3. 召回回路空转

**症状**：`retrieval_event` 表最近几日无记录，或全是 `system_noise`

**原因**：Hermes 插件未加载或未在使用中

**检查**：
```powershell
python scripts/recall_health.py
```

**解决**：
- 确保 Hermes 运行中加载了 `lantai-hook` 插件
- 触发词检查：短句（≤15 字）且无触发词（记得/上次/回忆/帮我查/之前/忘记/以前/曾经）不触发检索

### 4. 服务未启动

**症状**：API 调用返回连接拒绝

**解决**：
```powershell
python api_server.py
```
或 Docker 模式：
```bash
docker compose up -d
```

## 维护命令

| 命令 | 用途 |
|------|------|
| `python scripts/memory_overview.py` | 记忆概览（总数、待审、检查点） |
| `python scripts/reflect_observation_status.py` | 观察期门禁状态 |
| `python scripts/recall_health.py` | 召回健康度 |
| `python scripts/run_digest.py` | 手动生成每日盘点 |
| `python scripts/release_check.py vX.Y.Z` | 发布准备检查 |
| `pytest tests/ -q` | 全量测试 |
| `python scripts/run_forgetting_quality.py --check` | 评测集门禁 |
