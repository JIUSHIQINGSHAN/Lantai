# 02 - 反思运行可审计（reflect_run 落库 + 校准含运行记录）

Status: resolved
Type: task
Blocked by: (none)

## 目标

反思运行结果可审计：每次运行的水位/跳过/产出/LLM 失败/异常落库，
校准报告加入运行记录节（区分 空闲/正常/异常/LLM 失败），DB 增量迁移 v10。

## Answer

feat(reflect) `7a80288`：`ReflectRun` 表（reflect_run）+ `run_reflect_once` 异常留痕后原样抛出（调度器重试前可查）+ `collect_calibration_stats` 增加 `runs` 统计与渲染节；置信桶边界移入 settings（`7b59fff`，`DIGEST_CONF_BUCKETS`，ADR-0002 零硬编码）。

