# 调研报告：aiduMEM perf_baseline 工具形态

**日期**: 2026-08-02
**来源**: `C:\Users\Asus\Desktop\aiduMEM\tests\perf_baseline.py`（196 行，全量阅读）
**票据**: `.scratch/aidumem-port/issues/07-perf-baseline-tool.md`

---

## 1. aiduMEM perf_baseline 的完整实现

### 1.1 问句集

50 个问句，10 类 × 5 问，**纯中文中性样本**（不含真实身份信息）：

| 类别 | 样例 |
|------|------|
| 个人档案 | 用户生日、用户职业、用户所在公司… |
| 助手自身 | 助手名称、助手上线时间、助手人格设定… |
| 约定/口令 | 常用口令、问候语、专属称呼… |
| 时间线 | 第一次见面、重要转折点、去年这时候… |
| 运维 | 服务配置、API key 在哪、接口地址… |
| 关系 | 家人信息、同事、重要联系人… |
| 工具 | 工具箱、常用脚本、生图流程… |
| 内容/作品 | 作品名称、写作进度、完稿时间… |
| 规范 | 行为准则、操作前检查、权限级别… |
| 项目配置 | 项目名称、服务面板、部署域名… |

支持 `AIDUMEM_PERF_QUERIES=/path/to/queries.txt` 覆盖（每行一句）。

### 1.2 测试内容（3 项）

| 测试 | 调用端点 | 测量指标 | 维度 |
|------|---------|---------|------|
| Test 1 | `GET /facts/search?query=X&level=L&top_k=5` | 延迟 P50/P95、命中数 | L0/L1/L2 × 50 问 |
| Test 2 | `POST /facts/inject-context` | token 节省率（相对 L2 baseline） | L0/L1/L2 × 50 问 |
| Test 3 | `GET /facts/search?query=X&level=L0&top_k=3` | trajectory 逐步耗时（intent_ms, position_ms, scanned, hits） | 前 5 问 |

### 1.3 技术特征

- **零外部依赖**：仅用 `urllib`、`json`、`statistics`、`time`（不依赖 requests/httpx）
- **输出**：控制台表格 + `logs/perf_baseline.json`（含 timestamp、P50/P95/avg、token 节省率）
- **CI 建议**：源码注释写道"每次升级后跑本脚本，对比基线（不下降 10% 即 OK）"
- **环境变量**：`AIDUMEM_API_BASE`（默认 `http://127.0.0.1:8767`）、`AIDUMEM_PERF_TIMEOUT`（默认 10s）、`AIDUMEM_HOME`（日志目录）

### 1.4 L0/L1/L2 与 remembrance 的映射

aiduMEM 的 L0/L1/L2 是**亲密度分档**（default/tech/intimate），remembrance 无此概念。remembrance 的等价维度是 **lane**（fact/rule/experience/preference/chat/general）。

---

## 2. 移植建议

### 2.1 问句集：直接移植

50 个问句是中文中性样本，与 remembrance 的中文使用场景吻合。直接移植，但环境变量前缀改为 `REMEMBRANCE_*`。

### 2.2 端点映射

| aiduMEM 端点 | remembrance 等价 | 适配说明 |
|-------------|-----------------|---------|
| `/facts/search` | `POST /search` | remembrance 用 POST + JSON body，非 GET + query |
| `/facts/inject-context` | 暂无 | 需要在票据 10（Shell Hook）或 05（search_trace）中定义；perf_baseline 的 Test 2 依赖此端点 |
| trajectory | `/search_trace`（票据 05） | 未建，Test 3 依赖票据 05 的结论 |

### 2.3 维度映射

L0/L1/L2 → 用 `lanes` 参数替代（如 `lanes=["fact"]` vs `lanes=["fact","rule"]` vs `lanes=["fact","rule","experience","preference"]`），对应三档递进。

### 2.4 实施建议

1. **直接移植 50 问句**——它们是中性样本，无需自建
2. **适配 remembrance API 形状**：GET → POST、level → lanes
3. **Test 2/3 标记为 blocked**：依赖票据 05（search_trace）和票据 10（inject-context），待它们 resolved 后再纳入
4. **初期只跑 Test 1**：`/search` 延迟基线，不依赖未建端点
5. **纳入 CI 作为回归门禁**：`不下降 10%` 规则直接照搬

### 2.5 不建议自建问句集的理由

- 50 个问句覆盖了 10 个常见类别，作为**中性基线**足够
- 用户可用 `REMEMBRANCE_PERF_QUERIES` 覆盖为自己语料
- 自建问句集是额外的重复劳动，且没有比 aiduMEM 的样本更"正确"

---

## 3. 结论

| 问题 | 答案 |
|------|------|
| 50 问句从哪来？ | aiduMEM `tests/perf_baseline.py` 的 `DEFAULT_QUERIES`，10 类 × 5 问中文中性样本 |
| 直接移植还是自建？ | **直接移植 50 问句**，适配 remembrance API 形状（POST /search + lanes） |
| 输出格式？ | 控制台表格 + JSON 基线文件（`logs/perf_baseline.json`），照搬 |
| 纳入 CI？ | 是，照搬 aiduMEM 的"不下降 10%"回归门禁规则 |
| 阻塞依赖？ | Test 2/3 依赖票据 05（search_trace）和 10（inject-context），初期只跑 Test 1 |
