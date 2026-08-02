# 性能基线工具形态

Type: research
Status: resolved
Blocked by: —

## Question

aiduMEM 有 50 问句的 `perf_baseline` 工具，用于量化检索质量和性能。coalesce 上线后需要用它实测对比 LLM 调用次数下降。

需要调研：

1. aiduMEM 的 `perf_baseline` 具体实现：50 个问句从哪来？测什么指标？如何跑？
2. remembrance 是否直接移植这 50 个问句？还是根据自身数据分布自建？
3. 基线工具的输出格式：JSON？Markdown 报告？是否需要可视化？
4. 是否需要纳入 CI（回归测试）还是仅手动跑？

**AFK 子代理**：此票据为 research 类，由 /research 子代理并行攻关。

## Answer

**结论**：直接移植 aiduMEM 的 50 个中文中性问句，适配 remembrance API 形状（`POST /search` + `lanes` 参数替代 L0/L1/L2）。

关键决策：
1. **问句集**：直接移植 `DEFAULT_QUERIES`（10 类 × 5 问），用户可用 `REMEMBRANCE_PERF_QUERIES` 环境变量覆盖
2. **输出格式**：照搬——控制台表格 + `logs/perf_baseline.json`（含 timestamp、P50/P95/avg）
3. **CI 门禁**：照搬"不下降 10%"规则
4. **分阶段**：初期只跑 Test 1（`/search` 延迟基线）；Test 2（inject-context）和 Test 3（trajectory）依赖票据 05 和 10 resolved 后再纳入

**调研全文**：[`.scratch/aidumem-port/research/07-perf-baseline.md`](../research/07-perf-baseline.md)
