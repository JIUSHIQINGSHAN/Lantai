# 兰台运维手册

密钥泄露或定期轮换：见 [密钥轮换 Runbook](key-rotation-runbook.md)（本机 + 服务器双环境）。

## 先看司天（运行监控面板）

任何排障先开监控面板，别一上来就翻日志：

```powershell
# 浏览器
start http://127.0.0.1:8767/ui        # 侧边栏「司天监控」

# 或者命令行三件套
curl -s http://127.0.0.1:8767/monitor/overview | python -m json.tool | more
curl -s "http://127.0.0.1:8767/monitor/logs?only_problems=true" | python -m json.tool
curl -s http://127.0.0.1:8767/monitor/prometheus | head -40
```

**读法**（自上而下）：

| 看什么 | 指标 | 异常时怎么办 |
|--------|------|--------------|
| 服务活着吗 | `summary.status` / 告警区 | `critical` 优先处理，`scheduler_not_running` 先查启动日志 |
| worker 停没停 | 调度器表「状态」列 | 逾期 → `POST /monitor/workers/{name}/run` 手动补跑（v0.68 票 19 起 admin 专用：带 `X-API-Key: <env key>` 头） |
| 接口慢不慢 | p95 延迟 + 端点耗时排行 | 排行里 p95 高的端点通常是 LLM/精排调用，查端点可达性 |
| 有没有报错 | 5xx 错误率 + 最近问题请求 | `/monitor/logs?only_problems=true` 看具体端点与状态码 |
| 库在膨胀吗 | 存储占用 + 表行数 | 跑遗忘/沉淀；必要时备份后归档旧库 |
| 记忆质量 | 零召回率 | 见下文「零召回」与 ADR-0028（拾遗） |
| 检索有没有真被召回 | 记忆管道水位（潮波缓冲/待审积压） | 待审积压高 → 案牍台批量处置或收紧 `CANDIDATE_MIN_CONFIDENCE` |

**接入外部监控**：`GET /monitor/prometheus` 是标准文本格式（`lantai_*` 指标族），
Prometheus 直接抓即可；该端点走 API Key 鉴权，非回环部署记得带 `Authorization: Bearer <key>`。

**关掉遥测**：`MONITOR_ENABLED=false`——中间件零开销直通，面板接口仍可用（只是请求维度为空）。

## 常见问题

### 1. 反思观察期门禁卡住

**症状**：`reflect_observation_status.py --check` 返回 PENDING

**原因**：服务未整夜运行，APScheduler 定时任务未触发。

**检查**：
```powershell
python scripts/reflect_observation_status.py
```
输出示例：`窗口内合格定时运行天数：4/7（14 天窗口）`——需要 14 天滑动窗口
（`REFLECT_OBSERVATION_WINDOW_DAYS=14`）内至少 7 天（`REFLECT_OBSERVATION_REQUIRED_RUNS=7`）有定时运行。

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
- 若仍有大量低置信度候选，可调高 `CANDIDATE_MIN_CONFIDENCE`（出厂默认已 0.15；
  调高则更多低置信度候选被拦在待审队列外，注意与入库量的权衡——宁 miss 不脏写）
- 需要更严/更宽时在 `.env` 中覆盖 `CANDIDATE_MIN_CONFIDENCE`

### 3. 召回回路空转

**症状**：`retrieval_event` 表最近几日无记录，或全是 `system_noise`

**原因**：Hermes 插件未加载或未在使用中

**检查**：
```powershell
python scripts/recall_health.py
```

**解决**：
- 确保 Hermes 运行中加载了 `lantai-hook` 插件
- 触发词检查（gate 放行口径，ADR-0028 放宽）：纠错、自我指代、明确回忆
  （记得/忘记/回忆/查…记忆）、指代延续（上次/之前/继续）命中即检索；
  短查询含技术词（≥4 字）或成问句（≥5 汉字）也放行；都不命中才 no_signal。
  「帮我查」单独出现**不在**触发正则内（「查…记忆」形状才算明确回忆）

### 4. 服务未启动

**症状**：API 调用返回连接拒绝

**解决**：
```powershell
python api_server.py
```
或 Docker 模式（本仓库无 compose 文件，用 README「方式二：Docker 容器运行」的 `docker run` 单容器路径）：
```bash
docker build -t lantai:X.Y.Z .
docker run -d -p 8767:8767 -e API_KEY=... -e OPENAI_API_KEY=... -v /your/data:/data lantai:X.Y.Z
```

## 维护命令

| 命令 | 用途 |
|------|------|
| `python scripts/memory_overview.py` | 记忆概览（总数、待审、检查点） |
| `python scripts/reflect_observation_status.py` | 观察期门禁状态 |
| `python scripts/recall_health.py` | 召回健康度 |
| `POST /monitor/workers/digest/run`（服务在跑时；或 `GET /digest/today` 取当日结果） | 手动生成每日盘点（v0.68 票 19 起需 admin：请求带 `X-API-Key: <env key>` 头） |
| `python scripts/release_check.py vX.Y.Z` | 发布准备检查 |
| `pytest tests/ -q` | 全量测试 |
| `python scripts/run_forgetting_quality.py --check` | 评测集门禁 |
