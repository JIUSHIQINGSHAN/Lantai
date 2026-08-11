# 04 - 遗忘质量自测体系 + 中文记忆评测集（一年内档）

Status: resolved
Type: task
Source: docs/research/direction-research-report.md「一年内」档

## 目标

把兰台最强能力（Ebbinghaus 归档、Chronos 双时间轴、FTS trigram 中文容错）变成
可复现的数字主张：真实 DB 种子 → 真实遗忘 → 真实检索 → 维度化指标，补齐
「写入精度 / 遗忘质量 / 时效 无公共基准」的行业空白。

## 交付

1. `lantai/eval/forgetting_quality.py`：六项指标（stale_hit_rate / typo_recall_rate /
   fresh_recall_rate / temporal_order_accuracy / superseded_order_accuracy /
   superseded_residual_rate），诚实原则（无数据返回 0.0 不编造），finally 清理
   含 supersedes 关系边
2. `lantai/eval/chinese_memory_cases.py`：中文评测集 v1（13 case：typo×4 / fresh×3 /
   stale×2 / temporal×2 / superseded×2），全部 query 经 sqlite 直连验证 FTS 可命中
   （词边界型错字：FTS5 trigram 实测为整串子串匹配）
3. `scripts/run_forgetting_quality.py`：CLI 落盘报告
4. 首份报告 `docs/memory-quality/2026-08-11.md`

## 实测结论（FTS 兜底路径，最严格基准）

- typo_recall_rate=1.0 / fresh_recall_rate=1.0 / temporal_order_accuracy=1.0 /
  stale_hit_rate=0.0（归档零残留）✅
- superseded_order_accuracy=0.5、superseded_residual_rate=1.0：**真实缺口**——
  FTS 兜底路径下检索无 supersedes 边感知排序，BM25 空格 token/长度归一化会把旧值
  抬到新值之前（「API 密钥」用例实测旧值 config.py 排前）
- 附带修复：`search_fts` 剔除 <3 字符 token——2 字词（「密钥」）在 trigram 索引侧
  无法成词却毒化整条 AND 查询（修复前「API 密钥」查询整体失效）

## 下一步（另开 issue）

- hybrid 打分后加 supersedes 边降权/置顶，评测集回归断言该指标
- 向量路径启用后复跑：embedding 相似度应能区分新旧值，预期 superseded 指标改善

## 相关文件

lantai/eval/forgetting_quality.py、lantai/eval/chinese_memory_cases.py、
scripts/run_forgetting_quality.py、lantai/storage/fts.py、
tests/test_forgetting_quality.py、tests/test_fts_integration.py、
docs/memory-quality/2026-08-11.md