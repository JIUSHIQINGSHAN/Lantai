# Chroma 0.6.3 → 1.5.9 迁移

兰台使用内嵌 `PersistentClient`。1.0 起 Chroma 用 Rust 重写，打开旧目录可能自动升级 SQLite/HNSW 数据；**不得让 1.5.9 打开生产旧目录，也不得把升级后的目录交回 0.6.3**。本迁移采用显式导出/导入，旧环境、旧目录完整保留用于回滚。迁移不重新调用 embedding，不依赖外部服务。

依据：[Chroma 官方迁移说明](https://docs.trychroma.com/docs/overview/migration.md)、[collection configuration](https://docs.trychroma.com/docs/collections/configure.md)。

## 适配范围

- 旧集合 `remembrance_vectors` 继续优先使用；没有旧集合时用 `lantai_vectors`。迁移工具保留每个原集合名，不自动重命名，也不把两个集合混合。
- 新集合设置 `configuration={"hnsw": {"space": "cosine"}}`，不再向 metadata 写入 `hnsw:space`。Chroma 默认 embedding function 关闭，兰台继续显式传入向量。
- `list_collections()` 在 0.6.3 返回字符串、1.x 返回 `Collection`。包装层处理两种形态。
- `get()` 的 embedding 是 NumPy 数组，不能用其真值判断判断空；工具按 `is None` 和长度处理。查询包装层将行级空 metadata 转为 `{}`，距离转 Python `float`。
- 0.6.3 的 `configuration_json` 和 **collection metadata 都不能单独作为旧索引的迁移依据**。真实新建 cosine collection 会返回默认 `configuration_json.hnsw_configuration.space="l2"`；历史库还可能 collection metadata 标 cosine，但实际 VECTOR segment 没有 `hnsw:space`，沿用默认 `l2`。工具通过只读 SQLite 读取实际 VECTOR **segment metadata**，逐项映射为新 configuration；manifest 同时保留 collection 原声明、segment 实值和配置来源供审查。未知 `hnsw:*` 设置会拒绝迁移。此方案保留旧距离语义，不趁升级重设检索阈值。

## 工具保护与核对

使用 [migrate_chroma.py](../scripts/migrate_chroma.py)，其不加载兰台 settings、`.env` 或应用程序，不会隐式解析生产路径。各命令严格检查 Chroma 版本：export 要求 0.6.3，import/verify 要求 1.5.9。

`export` 要求明确提供旧库**冷副本**。Chroma 没有只读 `PersistentClient`，所以工具先只读复制该副本到全新 `--work-dir`，仅在那里调用旧版 `get/count/query`，任何引擎 housekeeping 都局限于工作副本。输入副本逐文件 SHA256 在复制前、复制后、导出后核对一致。工作目录、输入目录、bundle 目录必须互不包含；拒绝符号链接/目录联接。工具保留工作目录供审查，操作员在进程退出后手动清理。

`import` 要求目标目录**不存在**；既有空目录也拒绝，避免误开旧库自动迁移。`verify` 要求目标有本工具生成、与 bundle manifest SHA256 对应的导入标记，误传旧目录会在实例化 Chroma 前失败。导入失败只留下独立目标目录，不修改源。修正原因后使用另一个全新目标重试。

每个集合都导出并检查：

1. 全部 ids 集合、计数、embedding 维度、每个 float32 向量分量。默认 `--max-vector-ulps 0` 要求精确相等。仅 cosine 可显式用 `--max-vector-ulps 1` 接受每个分量至多 1 个 IEEE float32 ULP 的 HNSW 舍入；此时仍禁止符号变化/非有限值，并校验 float64 计算的模长差不超过 `float32.eps × 原模长`。报告如实标记 `vectors_float32_exact=false`、变化分量数和最大差，不声称逐位相等；原始 bundle 完整保留。l2/ip 即使指定该选项也仍要求精确。空新集合和删除后变空的集合的维度约束保留。
2. 每行 metadata、document、URI，包括缺失值、空字符串、整数、布尔值；不发明 metadata 默认值。自定义 collection metadata 保留，旧 HNSW 字段迁入 configuration。
3. 每个实际属主和一个不存在的属主，以及实际 owner/lane/domain 组合的过滤：`user_id == viewer OR user_id == ""`；逐个过滤核对全部允许 ids。缺失 user_id 不会被误当作空串共享记录。
4. 默认选取至多 3 个存量向量查询（可用 `--probe-count` 加大），对每个过滤重放 top-10 距离、返回数、ACL 边界。正式验收使用 `export --all-self-probes`，额外以全部旧向量逐一查询 top-10，记录旧/新自身命中数。对全部原向量计算 float64 精确 L2/cosine/ip 距离基准，要求新结果中每个返回 id 的距离误差不超过默认绝对上限 `1e-5`，新图每次查询的等距容忍 recall@k 不低于旧图。ANN 重建的等距替换或召回改善允许通过，旧/新排序距离差和 id 替换次数仍如实报告；`max_distance_delta` 是诊断值，准入以 `new_recall_not_worse` 和 `max_new_oracle_distance_error` 为准，不因找到更近邻居误报损坏。

迁移工具当前覆盖兰台默认 tenant/default database。发现其他 database 时拒绝导出，防止遗漏。工具将单个集合及探针装入内存，适用于本地兰台库；超大集合应先评估内存和探针数。bundle 含原始向量/metadata/文档，应与原库使用相同的访问保护，不提交真实 bundle 到 Git。

## 演练步骤（显式路径）

先用已有有效冷备演练。最终切换时停止所有可能写库的 API、worker、scheduler、MCP 进程，再同时冷备兰台 SQL 与 Chroma；保持停止直到核对和切换完成。热拷贝目录即使 SHA256 在检查期间不变，也不能证明备份一致。不要混用旧 SQL 冷备与新 Chroma 状态。

以下为 PowerShell 模板；替换所有路径为任务专用私有目录。旧解释器只运行导出，不给旧环境安装任何包。**Windows 的 `--work-dir` 与新 `--target` 使用完整 ASCII 路径**（例如 `D:\AI_Cache\chroma-migration\...`）；只读 source-copy/bundle 可在中文路径。实测本次 Windows 环境的旧/新 native HNSW 在非 ASCII 目录超过 1000 条落盘时索引文件不完整或重开失败；ASCII 目录通过。该约束针对迁移工具演练，不是更改产品存储路径策略。Linux `/data` 目标不受该 Windows 观测影响。

```powershell
& 'D:\path\old-venv\Scripts\python.exe' scripts/migrate_chroma.py export `
  --source-copy 'D:\private\cold-copy\.chromadb' `
  --bundle 'D:\private\chroma-export' `
  --work-dir 'D:\private\chroma-export-work' `
  --all-self-probes

& '.\.venv-upgrade314\Scripts\python.exe' scripts/migrate_chroma.py import `
  --bundle 'D:\private\chroma-export' `
  --target 'D:\private\chroma-1.5.9' `
  --hnsw-search-ef 1000

# 另一个进程重新打开目标，验证持久化后的数据。
& '.\.venv-upgrade314\Scripts\python.exe' scripts/migrate_chroma.py verify `
  --bundle 'D:\private\chroma-export' `
  --target 'D:\private\chroma-1.5.9'
```

`--hnsw-search-ef 1000` 是本次验收明确选择的新查询参数；不传时保留旧搜索宽度，参数不得低于旧值。选择和旧值写入导入标记/报告，独立 `verify` 按标记核对，不能靠事后未登记改参数通过。

导入关闭 writer 后重新打开目标进行首轮核对，成功在目标生成 `migration-report.json`；正式准入仍要求上面单独的 `verify` 进程退出码为 0。保留 stdout、manifest、输入冷备校验摘要和报告。检查 `ok=true`、ids/向量/metadata/ACL 一致、维度一致、`new_recall_not_worse=true` 且 oracle 距离误差小于阈值。额外在隔离运行环境把 `CHROMADB_PATH` 指向导入目标并进行检索、删除、恢复及业务补偿冒烟，禁止将演练应用指向生产 SQL。

切换由部署流程把新运行时指向已验证的新目录（或将新目录放入新的部署根）。不得覆盖、原地升级或清空旧目录。切换后若有新写入，回滚会丢弃这些新写入；须在短观察窗保持写入冻结，或者先设计有审计的增量回放。回滚使用旧代码 + 旧运行时 + 同一冷备时点的旧 SQL/Chroma。1.5.9 的磁盘格式向后不兼容，不能靠降级 Python 包读回。

## 测试与复现

[test_chroma_upgrade.py](../tests/test_chroma_upgrade.py) 直调真实 Rust 存储，覆盖 CRUD、cosine 距离、集合名保留、数组结构、空库/空过滤、嵌套 ACL、维度错误后原数据仍在，以及真实维度失败后的向量删除/补偿恢复。仅外部 embedding 服务使用确定性替身。

[test_chroma_migration.py](../tests/test_chroma_migration.py) 使用 [0.6.3 真实导出夹具](../tests/fixtures/chroma-0.6.3/manifest.json) 导入真实 1.5.9，核对全量行并测试目标误用、校验和破坏、向量/metadata/ids 漂移等拒绝路径。夹具是合成数据，包含 3 个 collection、4 行向量、原 `remembrance_vectors`、无 metadata 行、全新空集合、删除后仍保留 3 维的空集合。夹具仅移除源机器路径和文件摘要，不改数据或旧查询距离。

```powershell
& '.\.venv-upgrade314\Scripts\python.exe' -m pytest tests/test_chroma_upgrade.py tests/test_chroma_migration.py tests/test_infra.py tests/test_dedup_ownership.py tests/test_promoter_rollback_vector.py --basetemp 'D:\private\pytest-chroma' -q
```

## 2026-11-17 实际冷备演练

运行时为旧 CPython 3.11.15/Chroma 0.6.3 → 新 CPython 3.14.10/Chroma 1.5.9。Python 3.15.2 的依赖链被 onnxruntime 1.29 官方 cp315 wheel 缺失阻断，主代理选择 3.14.10；没有安装或改动旧虚拟环境。

使用主代理提供的有效冷备，旧 `remembrance_vectors` 共 930 条、1024 维。发现 collection metadata 标 cosine，VECTOR segment metadata 为空，实际旧查询是 squared-L2。工具保留实际 `l2`；产品 `ChromaVectorStore()` 重开导入库后仍为 `remembrance_vectors/l2/930`，新建默认 cosine 没有覆盖已有配置。

独立新进程 `verify` 严格模式结果：

| 检查 | 结果 |
|---|---|
| 全部 ids、维度、计数 | 930 × 1024，完全一致 |
| 向量分量与模长 | float32 精确相等，变化向量/分量 0，最大 ULP/绝对差/模长差 0 |
| 每行 metadata / documents / URIs | 全部一致 |
| 全量自身查询 | 930 次；旧库、新库各 930 次命中自身 |
| ACL/过滤成员集 | 18 种，逐个全量 ids 相等 |
| 总查询数 | 984（930 自身 + 54 抽样过滤） |
| 最大距离差 | 2.6512145956392175e-7，小于 1e-5 |
| top-k id 集合 | 11 次等距边界替换，排序距离/共同 id 距离通过；未声称 id 顺序完全相同 |
| 源副本 | 导出前/后逐文件 SHA256 不变 |

旧 `ef_search=100` 下，重建 ANN 图的 top-k 边界出现过 0.003765 / 0.004510 的差异，独立进程结果并非稳定一致。主代理授权本次新目标**显式**设 `ef_search=1000`；metric 仍为 `l2`，向量不变。提高搜索宽度通常提高召回、增加查询计算量；默认迁移不调整该值。最终准入必须独立新进程 `verify`，并与精确距离基准比较，不因新 ANN 找到了旧图漏掉的更近邻居而拒绝。

用全部导出向量的 float64 暴力 L2 作为 oracle，984 次旧/新查询的等距容忍 recall@10 均为 1.0，单次最低也为 1.0，新库不劣次数 984/984；返回 id 的距离对 oracle 最大误差 5.508748381899409e-7。另 3 个确定性查询用 `n_results=930` 枚举全库，ids 集合完全一致、最大距离误差 5.534899734094267e-7。

超过落盘阈值的 1100×32 合成旧库也完成旧导出→Rust 持久化→新进程重开：1118 次查询，旧 recall@10 均值 0.9997316636851519、最差 0.9，新库 `ef_search=1000` 均值及最差均为 1.0、无一查询退化，旧/新自身命中各 1100。其新邻居优于旧图，不能要求排序距离逐项完全相等。

在另一个真实 Rust 对照实验中，相同 220 条随机向量写 `l2` 全部精确，写 cosine 有 76 条发生至多 1 ULP 舍入。这是可选 `--max-vector-ulps 1` 的依据；上述 930 条真实冷备验收未使用容差。原始向量 bundle 保留，迁移不重新 embedding。

最终 19 个新增 Chroma 测试加 11 个脚本编码测试合计 30 passed；此前 56 个既有基础设施、ACL、回滚、删除补偿回归已通过。新增测试包括显式 ef 在新进程持久化验证、真实 cosine 1 ULP 的严格拒绝/显式接受/大改拒绝、CLI 非 ASCII 目录前置拒绝。相关文件 `ruff check` 与 `ruff format` 已通过。

最后一轮真实 930 条和合成 1100 条的独立 `import`、`verify` 四条 CLI 命令均退出 0，数据向量全部精确，未使用 ULP 容差。代码与副本演练通过不表示生产已经迁移；最终部署应重新生成同时点 SQL/Chroma 冷备，重复完整步骤，以保留本次冷备之后的写入。
