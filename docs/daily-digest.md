# Daily Digest 每日盘点报告

每日清晨自动生成一份记忆盘点报告，Hermes 早晨首次对话即可读到摘要。

## 报告内容

报告文件：`<数据目录>/docs/memory-digest/YYYY-MM-DD.md`。数据目录 = `LANTAI_HOME`（未设时为仓库根，仅仓库形态）：仓库形态即 `docs/memory-digest/`，wheel/容器形态随 `LANTAI_HOME`（如 `/data/docs/memory-digest/`）。可用 `DIGEST_OUTPUT_DIR` 显式改输出目录（优先于数据目录推导）。

| 统计 | 说明 |
|---|---|
| 新增记忆 | 当日创建的记忆数（本地日历日） |
| 修改记忆 | 当日有更新且晚于创建的记忆数 |
| 记忆总量 | 当前全部记忆数 |
| 待审候选 | 锦囊队列总数 + 今日新增；>0 时报告附处理提醒 |
| 今日归档 | 自动 TTL 归档数 + 当日创建即归档数 |
| 今日检索 | 检索次数、零结果数、系统噪音数、平均延迟 |
| 反思提案 | 当日反思产生的提案数（自动应用 / 待审 / 拒绝细分，`digest_worker._aggregate_reflection`） |

## 触发方式

- 定时任务：每天 `DIGEST_CRON_HOUR`（默认 UTC 15:55 ≈ 上海 23:55 日末，票 31：触发点贴近所报告本地日的日界，早晨触发只覆盖当天前几小时）自动生成；可改 `DIGEST_CRON_HOUR`，或 `DIGEST_ENABLED=False` 关闭。
- 按需读取：Hermes 用 MCP 工具 `get_digest`；REST `GET /digest/today`（需 API key，未生成则先生成）。

## 与待审队列的关系

报告中的待审数字来自锦囊队列（pending_review）。处理入口：`GET /candidates/pending` + `POST /candidates/{id}/review`，或对 Hermes 说「查看待审候选」；7 天未处理自动归档为 rejected。
