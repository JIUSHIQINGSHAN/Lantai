# 02 - 反思运行可审计（reflect_run 落库 + 校准含运行记录）

Status: resolved
Type: task
Blocked by: (none)

## 目标

反思运行结果可审计：每次运行的水位/跳过/产出/LLM 失败/异常落库，
校准报告加入运行记录节（区分 空闲/正常/异常/LLM 失败），DB 增量迁移 v10。

## Answer

feat(reflect) `7a80288`：`ReflectRun` 表（reflect_run）+ `run_reflect_once` 异常留痕后原样抛出（调度器重试前可查）+ `collect_calibration_stats` 增加 `runs` 统计与渲染节；置信桶边界移入 settings（`7b59fff`，`DIGEST_CONF_BUCKETS`，ADR-0002 零硬编码）。

审查修复（v11）：rejecter 失败单独计数 `rejecter_failed`（迁移 v11），LLM 失败口径 = curator 或 rejecter 任一失败；`_record_reflect_run` 落库失败打日志不静默；异常路径尽力补水位；校准运行节补「正常但零产出」分类。启动补跑覆盖 digest+reflect 两个每日 cron 任务系观察期保底目标的有意设计（非范围蔓延）。

