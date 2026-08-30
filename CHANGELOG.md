# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **开发工作流标准化**: 确立《研发工作流规范》（`docs/development-workflow.md`），基于六阶段标准（需求立项拆解、5 步根因诊断、架构与命名治理、TDD 先导与核心函数不 mock 冒烟、代码审查门禁、版本收口与发布闸门），并作为 `AGENTS.md` 强制规则。
- **拾遗检索韧性与多级降级（ADR-0028）**: 
  - 混合检索 `hybrid_search` 嵌入异常防护：外部 Embedding API 鉴权 401/网络超时/连接中断时不挂死，平滑降级至 `_keyword_fallback` 本地 FTS5 + BM25 关键词检索；
  - 降级候选提取补充 SQLite LIKE 子串匹配，彻底解决 < 3 字符短词（如 "电脑"、"显卡"、"测试"）无法触发 FTS5 trigram 分词导致的零召回；
  - `SearchReq` 增加 `force: bool = False`，支持显式透传直接绕过闸门检索；
  - 「拾遗」正式登记 `CONTEXT.md` 词汇表（ADR-0013 意象池「拾遗」= 唐代谏官官职，取「拾遗补阙、失落必还」之意）。
- **察窗观察期滑动窗口（ADR-0027，v0.16.0）**: `scripts/reflect_observation_status.py` 支持 `reference_date` 参数，反思观察期由连续口径改为滑动窗口内合格天数统计。

### Fixed
- **相关性闸门短查询与自指校准（ADR-0028）**:
  - `_BASE_SELF_REFERENCE` 正则收录 "大哥" 等项目核心自指，使 "大哥电脑配置" 准确识别为自指；
  - 社交结束语 `NO_MEMORY_PATTERNS` 增强支持多词组合（如 "好的谢谢"、"好的好的"）；
  - 内容查询放行技术/领域专业词短查询（如 "什么是事件驱动架构"、"华硕天选三显卡"），消除武断的 15 字符硬门槛对短实词的误杀；
  - 修复 `tests/test_reflect_observation_status.py` 静态时间戳导致的滑动窗口老化失效。

## [0.15.2] - 2026-08-27

### Added
- **案牍控制台 Phase 1**: `/ui` 重构为单维护者记忆运营工作台，新增七类案牍只读投影、确定性分区/排序、详情检查器、批量拒绝/延期/整理、worker 对应重跑、吉金/漏窗响应式外壳；前端采用 FastAPI 同源托管 HTML/CSS/ES Modules，无构建步骤，五个旧控制台路由继续保留。新增 ADR-0025 与 `.scratch/console-workbench/` 规格票据。
- **候选延期**: `memorycandidate` 增加延期与单步撤销留痕，schema v14；支持 3/7 天延期，最长不超过首次创建后 30 天。
- **反思观察门槛可审计**: `reflect_run` 增加 `source`（scheduled/manual/unknown）并迁移至 schema v13；定时任务与 MCP 手动运行分别写入来源，旧记录保守标为 unknown。新增 `scripts/reflect_observation_status.py`：默认只读报告连续合格定时运行次数，`--check` 未满足 7 次时以失败码阻断发布准备。

### Changed
- **候选审批改为两阶段**: `candidate_review approve=true` 只创建 pending 提案，不再立即应用；最终写入必须通过提案裁决。REST、MCP、控制台与测试统一该语义；拒绝类裁决要求填写理由。
- **v0.15.2 代号登记**: 计划版本代号定为「绳墨」——《礼记·经解》「绳墨之于曲直」，对应本版的校准、门禁与收口；README 测试说明改为以 CI 为准，ADR 索引更新至 0024。

### Fixed
- **curator 零产出根因修复（A 遗留，2026-08-15）**: `REFLECT_CURATOR_SYS` 补显式空提案契约（"If nothing warrants a change, return exactly {\"proposals\": []}"）——实测 Qwen3-8B 在缺该指令时对严格 JSON 妥协返回 `{}`（零产出主因）；补指令后正常返回严格 JSON 且产出真实提案。`curate_failed`（2/3 运行）为网络瞬断偶发，已有空降级留痕。观察期校准从本次修复起重新积累有效样本
- **悬空链接清理（v0.15.2 D1）**: `reflection-module-spec/prompt` 对已删生成报告 `docs/memory-quality/2026-08-11.md` 的引用改为内联数字 + 修复指向（报告按生成归档策略移出 git）

### Changed
- **性能基线首份（v0.15.2 D2）**: `scripts/perf_baseline.py` 实跑——20 问全 200，P50=2062.8ms / P95=2089.7ms；延迟主因外部 embedding API（~2s/次），本地管线毫秒级；报告 `docs/memory-quality/perf-baseline-2026-08-15.md`（生成报告本地留档）
- **评测集 v3（80 case，v0.15.2 C3）**: `chinese_memory_cases.py` 50 → 80 case（typo×23 / fresh×18 / stale×14 / temporal×13 / superseded×12，5 类内扩不动 GATES/runner）；防漂移锁定测试（test_memory_quality_spec.py）计数自动跟随；规格文档/白皮书 8.3 同步；门禁实测 PASS
- **校雠实质新词扩展信号（ADR-0023，v0.15.1 C1）**: `classify_relation` 无新增值分支加扩展判定——旧锚点零丢失（dropped 空，改写是替换非扩展）+ 新增实质词 ≥ `DEDUP_EXTRA_ANCHOR_LIMIT`(2) → 判 **update 提案**（有刹车，不吞内容）——修复 ADR-0019 锚点比非对称（old⊆new 恒 1.0）导致的扩展事实误 merge 吞并；不扩技术名值类（列表漂移，宁 miss）。36 对回归不回归（改写对 dropped 非空）+ 新增扩展对/对照组 3 例
- **参商单字否定对候选探测（ADR-0024，v0.15.1 C2）**: `conflict_rules.check_negation_pairs`——token 级子串探测 是/不是、会/不会、能/不能、有/没有、要/不要 交叉命中 → **候选**（不落硬规则）→ `decision.py` 对该记忆调 LLM 矛盾检测裁决；LLM 判非矛盾/失败 → 放行（宁 miss）。jieba 并词（"我会"→一词）场景由此捕获；"开会" 类误候选由 LLM 澄清。既有回落路径与否定路径的 LLM 调用补 try/except 韧性（防御一致性）

### Fixed
- **反思校准口径修复（A 项收口，2026-08-15）**: `digest_worker._aggregate_reflection` 反思提案标识由 `candidate_id IS NULL` 收紧为 `decided_by == 'reflect'`（reflector 落 `decided_by="reflect"`，与 evolve auto / autodream 区分）——此前 evolve 自动提案误计为「反思提案」（真实库 16 条 duplicate-merge 被误计），校准输入污染。拒绝原因统计同口径。测试：`test_digest.py::test_non_reflect_proposals_excluded` 回归断言 + 既有反思用例种子同步 `decided_by="reflect"`
- **校准窗口竞态修复**: `collect_calibration_stats` 窗口边界秒级截断 + 1s 顶边过悬——微秒精度采样与写入同秒撞界致 `run_at < end` 偶发漏数（test_digest 配对 ~50% flaky，复现后修复，配对 20/20 稳定）
- **观察期数据门判定（8/15）**: 3 次运行 0 产出、2 次 curator LLM 失败 → 样本不足，`REFLECT_IMPORTANCE_POOL`(5.0) / `REFLECT_AUTO_APPLY_CONF`(0.7) / `REFLECT_MIN_CONFIDENCE`(0.5) 维持 dry-run 值（宁 miss 不脏写），`REFLECT_STALE_SCAN_ENABLED` 维持 False；观察期延长至 7 个完整运行日（先修 curator LLM 失败根因）。校准报告 `docs/memory-quality/reflect-calibration-2026-08-15.md`

### Added
- **底本闭环（ADR-0022，v0.15.0 B 项）**: shell_hook serve 协议新增 `{"type":"checkpoint"}`（会话启动注入上次会话五段快照，独立通道不占每轮召回预算）与 `{"type":"checkpoint_write","session_id","blocks"}`（插件会话结束落快照，同库同语义）；Hermes 插件 `pre_llm_call` 会话首轮注入底本（与查询长度/触发词无关，每会话一次，有界标记集），`on_session_end` 用会话缓冲构建五段块落库（宁 miss：在做=末条消息、下一步/决策/待办句式命中才填、工作区恒空）；`build_session_blocks` 纯函数可测。测试：serve 协议分支真实库（test_checkpoint_service.py）+ 插件首轮/落块/纯函数（test_hermes_plugin.py，子进程 mock）
- **底本五段会话快照（ADR-0021，Fog 项收口）**: `lantai/services/checkpoint_service.py`——五段块（在做/下一步/工作区/决策/待办，移植 aiduMEM checkpoint.py 窄版），上下文压缩时 `write_session_checkpoint` 写入、下次会话启动 `inject_checkpoint_context` 注入（>30 天自动标注陈旧）；同 session 重写即替换、保留最近 5 会话（`CHECKPOINT_MAX_SESSIONS`）、块 <3 字符不落 / >600 截断（宁 miss 不脏写）；schema 迁移 v11→v12（session_checkpoint 表）。REST `POST /checkpoint` + `GET /checkpoint/latest` + `GET /checkpoint?session_id=` + `POST /checkpoint/cleanup`（受保护）；MCP `checkpoint_write`/`checkpoint_latest`（工具 40→42）。「底本」登记 CONTEXT.md 词汇表（ADR-0013）。测试 `tests/test_checkpoint_service.py`（纯函数不 mock + 真实 SQLite）+ 迁移断言 v12
- **中文记忆评测集 v2（50 case）+ 纳入 CI（路线图收口）**: `lantai/eval/chinese_memory_cases.py` 13 → 50 case（typo×15 / fresh×12 / stale×8 / temporal×8 / superseded×7，dataset 名 chinese-memory-v2）；错别字 case 统一「去首字」模式保证 FTS trigram AND 链确定性命中，陈旧 case 按 lane 半衰期（chat 90d / preference 200d）保证归档；`GATES` 门槛不变，`run_forgetting_quality.py --check` 实测 PASS；新增 `.github/workflows/tests.yml`——push/PR 全量 pytest + 遗忘质量门禁（供应链纪律：actions 锁 SHA）。测试 `test_forgetting_quality.py` 样本计数 13→50
- **salience 冲突降权 + 反义词碰撞（ADR-0020，Fog 项收口）**: `gate/conflict_rules.py` 新增 `check_antonyms`（jieba 词级互斥，8 对默认反义词，settings 可配；单字否定对因 jieba 并词默认不启用）——"喜欢咖啡"vs"讨厌咖啡"、"支持 X"vs"反对 X" 零 LLM 确定性命中；`gate/decision.py` 分流：确定性冲突命中低 salience 旧记忆（importance < 0.4）→ 降权 0.2（Checkpoint 可回滚）+ ConflictEvent kind=salience_demote status=resolved + 候选放行走提案链（有刹车）；高 salience / LLM 矛盾维持 archive_conflict 人工裁决。测试：反义词双向/词级不误伤/开关 + 降权/高 salience/LLM 不分流（test_conflict_rules.py，规则层不 mock）
- **autodream 7 天周期蒸馏（Fog 项收口）**: `lantai/workers/autodream_worker.py::run_autodream_scheduled`——后台周期蒸馏落 pending 提案（decided_by="autodream"，人工闸门裁决，宁 miss 不脏写）+ `record_run("autodream")` 可观测；scheduler 注册 interval job（`AUTODREAM_CRON_DAYS`=7 默认，settings 可配，AUTODREAM_ENABLED 门控）。测试：scheduler 注册断言（interval/days=7/开关）+ worker 真实库落库冒烟（test_scheduler.py / test_autodream.py）
- **arm64 Docker 镜像（Fog 项收口）**: `.github/workflows/ci.yml` `platforms: linux/amd64,linux/arm64`——tag 推送构建双架构镜像

### Changed
- **校雠三态去重升级（ADR-0019，结构判别）**: 实测（36 对 / 3 类中文样本，真实 bge-m3）证明单一余弦阈值无法分离 merge/update——更新类 5/12 被误判 merge 静默吞掉新值。升级为两相位：① 余弦预筛（提取前，≥ `DEDUP_PRESCREEN_MERGE`=0.95 直合零 LLM、< 0.65 insert）；② 中带提取后结构判别（`lantai/gate/relation.py::classify_relation`，锚点 + 归一化值规则，中带 LLM 兜底、失败降级 insert——宁 miss 不脏写）。`DEDUP_MERGE_THRESHOLD` 默认 0.80 → 0.90（fastpath 路径阈值）；`DEDUP_STRUCTURAL_ENABLED` / `DEDUP_STRUCTURAL_LLM_ENABLED` / `DEDUP_ANCHOR_HIGH` / `DEDUP_ANCHOR_LOW` 新增。回归样本 36 对入 `tests/test_dedup_relation.py`（规则层不 mock）+ `tests/test_dedup_flow.py` 两相位接线。票据：白皮书路线图「去重阈值实测校准」，prototype 见 `.scratch/dedup-threshold-calibration/`

## [0.14.0] - 2026-08-13

- **版本代号「缥缃」**: 丝帛书衣，代指书卷——贴合兰台档案/书卷定位；登记于 `CONTEXT.md` 词汇表与 ADR-0013 版本代号登记。

- **版本上传规范流程（发布门禁 + 人工闸门）**: `docs/release-process.md` 定义从版本号收口到 GHCR 镜像验证的完整流程；`scripts/release_check.py` 只读门禁核对 pyproject / README / FastAPI / MCP serverInfo / CHANGELOG 版本一致，并检查 Git 分支 / 工作区干净 / tag 不重复 / origin 存在（`--online` 时同时查远程 tag）；存量版本号不一致收口到 v0.3.7（FastAPI version / MCP serverInfo / README Docker 示例）。发布上传（push tag）保持人工闸门，Agent 只检查/准备。
- **v0.14 双主题换肤（吉金 + 漏窗，2026-08-12，承接 v0.13 书卷换肤赛道）**: 五式预览（玄墨/天青/书衣/吉金/漏窗，见 `.scratch/v0.14-style-preview/`）用户选定吉金+漏窗，按 ADR-0013 登记命名后落地——`lantai/api/routes_ui.py` 六个面板全局双主题（`[data-theme]` CSS 变量覆盖层，零侵入）：吉金（默认）=玄青拓片底 `#1c2430` / 铜绿 `#3e7a6b` / 鎏金 `#b08a3e` / 朱砂 `#a33b2e` + 云雷纹饰带 + 楷体/宋体；漏窗=绢黄底 `#e9dfc6` / 黛青 `#2f4f4f` / 石绿 `#4e8d7c` / 竹青 `#6f9e8a` + 回纹画框 + 月洞门形卡片 + 行楷/宋体；右上角主题切换钮（localStorage `lantai-theme` 持久化 + `?theme=louchuang` 深链）；记忆星图 SVG 配色改读 CSS 变量（lane/edge/场景/来源/label）随主题重绘；五式名已登记 `CONTEXT.md` 词汇表与 ADR-0013 映射表。UI 面板测试 23 例全绿。票据 01

- **v0.13 书卷·中国色换肤（2026-08-12，借鉴 zhongguose 全谱 526 色）**: 全局 CSS 变量换肤（汉白玉底 `#f8f4ed` / 象牙白卡 / 油绿墨 `#253d24` / 竹绿主色 `#1ba784` / 赭石·靛青·夹竹桃红·瓦松绿·玫瑰灰六 lane 色 / 琥珀黄·朱红 edge 色 / 8 色场景调色板）；**记忆星图防重叠布局重写**（`lantai/api/routes_ui.py::layout`）：画布 1000×700 → 1800×1300，场景组按成员数比例分槽 + 5 层半径（每成员 +26），独立记忆每环 8 个、半径 330 起每环 +40，来源节点最外环角度排序 + 最小 4° 贪心间隔，标签白描边 + 10 字符截断；真实数据 40 节点 0 重叠（minD 36.3px，旧版 34 对重叠 minD 2.7），90 节点压力数据同样 0 重叠 0 出界。票据 01
- **v0.12.1 修复（2026-08-12）**: 根路径 `/` 由 404 改为 307 跳转 `/ui` 控制台——浏览器直接打开 `http://127.0.0.1:8767/` 即可进站（此前根地址 404 表现为「网站打不开」）。
- **v0.12 目识·截屏入忆（目识闭环，借鉴 aiduMEI tools/shot.js 显式触发思路）**: `scripts/screenshot_memory.ps1`——剪贴板截图（Win+Shift+S）或 `-FromFile` 图片 → PNG → base64 data URI → 既有 `POST /add media_url` 通道（title/lane/BaseUri/ApiKey 参数化，`-DryRun` 只构造不写库，pwsh7 MTA 自动 STA 重入）；`validate_media_url` 增强 data URI 严格校验（MIME 白名单 png/jpeg/webp/gif、base64 严格解码、解码后 ≤ `MEDIA_DATA_URI_MAX_BYTES`=10MB，宁 miss 不脏写）；`AddMemoryReq.media_url` max_length 2000 → 15_000_000（截屏 data URI 可达 MB 级字符）。测试 `tests/test_vision.py` 10 例（+3：data URI 规则/超限/schema 长 URI）。票据 01
- **v0.11 烽燧 记忆广播链（借鉴 aiduMEI memory_broadcast /recall_chain 窄版）**: `lantai/ops/recall_chain.py::build_recall_chain(seed_text, max_depth=3, branch=3, min_score=0.3, total_max=20)` 纯函数——seed 逐层 BFS 传播：每层以当前 seed 集调 `hybrid_search(top_k=branch*3, use_rerank=False)` 后链内按分数降序取 branch 条，命中记忆的 content 作下一层 seed；入选需 score≥min_score、非自匹配（文本归一化相等或 jieba 词集合余弦≥0.9，锚点整链排除）、id 跨层去重、总量封顶；单条搜索失败只缺层不阻断（宁 miss 不脏写）；`validate_chain_params` REST/MCP/纯函数三处共用，非法参数抛 ValueError 不静默修正；REST `GET /recall/chain`（只读）+ MCP `recall_chain`（工具 39 → 40）；明确不吸收：作者版 workspace 冷记忆自动清理（兰台 archived 语义已有）、J-lens 整包（search_trace/recall_report 已覆盖）、Ignition 双路径（trace 体系已覆盖）。测试 `tests/test_recall_chain.py` 7 例（真实 SQLite+FTS + 本地 ngram 嵌入 + 假向量库，仅替换外部网络；BFS/去重/自匹配/封顶真实执行）。票据 01
- **v0.10 目识 Vision 多模态（借鉴 aiduMEI v18.3「多模态感知纪元」）**: `/add` 与 MCP `add` 支持 `media_url`（仅 http/https/data，`validate_media_url` 白名单校验，兰台不直接 fetch 图片零 SSRF 面）；`VISION_MODEL` 空时回退 `LLM_MODEL`，`vision_caption` 复用单一 LLM 网关（OpenAI 兼容 chat.completions + image_url，temperature=0.1 / max_tokens=500）；`build_vision_memory`：content 空 → caption 作正文，非空 → 存 `metadata.vision`，失败抛 ValueError 不落失败文本（宁 miss 不脏写）；provenance 记 `vision-caption` + 附加字段（media_url / vision_model）；content/media_url 二选一校验（同给 / 皆空 / <10 字拒绝）。明确不吸收：作者版失败落「图片解析失败」字符串（脏写）。测试 `tests/test_vision.py` 7 例（真实 SQLite+FTS 全链路，仅 mock Vision 外部网络）。票据 01
- **v0.9 code-review 两轴修复收口（2026-08-12）**: `/ui/map` 补 `info` 变量定义（悬停详情 ReferenceError 硬 bug）、renderStats 拼接优先级修正、死代码 `Math.min(13,11)` 清理；scene 成员同色聚簇（场景调色板，spec 原文语义兑现）；点击记忆节点跳 `/ui/recall?q=label`（recall 页支持 `?q=` 预填自动检索，「点击跳档案检索」落地）；limit 校验提取 `ops/graph.validate_graph_limit` 三处共用（REST/MCP/纯函数，build_graph 非法 limit 抛 ValueError 不静默钳制）；`graph_route`/`build_graph`/`get_graph` 补类型注解。票据 01（code-review 收口）
- **反思运行可审计（v10）**: `ReflectRun` 表落库每次反思运行（水位/跳过/产出/LLM 失败/异常，idle 与异常不静默），`run_reflect_once` 异常留痕后原样抛出（调度器重试前可查）；校准报告新增运行记录节（运行次数/空闲/异常/LLM 失败/产出提案），DB 增量迁移 v9 → v10。票据 observability 02
- **重构（收口）**: verbatim 直存共用构造器 `build_verbatim_item`（`add_raw_memory` 与冷启动导入同源去重）；ACL 兜底 lane 改读 `RAW_MEMORY_DEFAULT_LANE`；digest 置信桶边界移入 settings（`DIGEST_CONF_BUCKETS`，ADR-0002 零硬编码）；`import_session_jsonl` 的 `would_import` 统一预览口径（真实模式不随 ingest 错误缩水）
- **MAP 记忆星图（v0.9，借鉴 aiduMEI v18.3.0 MAP 面板窄版）**: `lantai/ops/graph.py::build_graph(session, limit)` 纯函数（零 DB 零 LLM）——节点 = active 记忆（仅参与 MemoryEdge 或属 scene 才入选，孤立记忆不上图）+ 参与边的来源文档 RawDocument（doc_*，带 title/url，出处可溯）；链接 = MemoryEdge（supports 绿 / refines 蓝 / contradicts 橙 / supersedes 红），两端在入选集合才保留（跨池边、指向 archived/池外端点丢弃），scene 名称映射 + node_type/lane/relation 统计；REST `GET /graph`（受保护，limit∈[1,500]）；MCP `graph_view`（工具 38 → 39，只读）；`/ui/map` 零依赖内联 SVG 放射布局（6 lane 扇区 + scene 聚簇 + 来源文档外环矩形贴邻接记忆 + 悬停详情 + 点击记忆跳档案/点击来源开 URL，无外部请求）；`/ui` 入口第五面板。明确不吸收：layer1_selfcheck 容量>80% 自动合并（违背宁 miss 不脏写）、instinct_graduation 自动毕业删原文（v0.7 crystal 已覆盖）。票据 01
- **review 修复（两轴 code-review 收口）**: /import/jsonl 补 lane 级 ACL 校验（绑定 agent 越界 lane 行记 errors 不落库，宁 miss 不脏写）；verbatim 导入时间戳统一归一化为 naive UTC（与摄取链同语义，digest 等 naive 区间比较不再静默偏移）；digest 反思统计统一 created_at 窗口并加 other 兜底（合计恒等于当日提案数）；erify_agent 类型标注修正；settings 注释错贴修复；	est_acl 检索用例改真实 SQLite 检索（不 mock 内部逻辑）；v8 迁移断言适配 v9 迁移链。
- **MCP 工具扩容（第二波，借鉴 aiduMEI v18 工具面 37 个反查兰台已有服务，21 → 28）**: 新增 `mem_recent`（最近记忆只读，按更新时间倒序）、`mem_stats`（overview 聚合：总数/分布/待审候选/检查点/待审提案）、`mem_health`（深度健康：SQLite + 向量存储，不触发外部 LLM）、`autodream_report`（蒸馏预演 dry-run 不写库）、`autodream_trigger`（执行一轮蒸馏落 pending 提案，宁 miss 不脏写）、`proposals_list` / `proposal_decide`（待审提案查看/裁决，approve 先落 Checkpoint 可回滚、reject 记 decision_reason）；`tests/test_mcp.py` 追加 8 例（不 mock 冒烟：真实 SQLite+FTS，仅 mock embedding/向量存储）；明确不吸收：`mem_delete`/`mem_delete_all`（硬删除无审计链）、`mem_update`（原地编辑以 Checkpoint 回滚替代）、`session_*`/`code_*`/`crystals_*`/`knowledge_tree`（会话归宿主、Code Graph 正交、树状/结晶为后续赛道）。票据 09
- **工具面第三波（v0.8，作者 aiduMEI 37 工具反查收尾，34 → 38）**: `reflect_run`（包装既有 `reflector.run_reflect_once`——反思本轮唯一缺失入口，Agent 可主动触发，高置信 auto-apply / 中风险 pending）、`mem_usage`（`ops/usage.collect_usage` 服务提取，REST `/usage` 与 MCP 共用，7 天每日新增缺日补零）、`core_memory_get`（核心记忆块只读）、`verbatim_search`（原文直存专用检索通道，FTS+向量不进混合召回）；明确不吸收终判：`mem_update`/`mem_delete`（无审计链）、`mem_observe`（add_dialogue 覆盖）、`mem_persona`（core-memory identity 覆盖）、`session_*`/`code_*`（归宿主/正交）。票据 01
- **记忆分类树（v0.7，借鉴 aiduMEI TreeMemory 窄版）**: `lantai/services/tree_service.py`——`MemoryNode` 父子表 + `node_path` 唯一路径 + depth 前缀查询，`memoryitem.tree_path` 显式挂载（v9 增量迁移，前缀统计不靠名字匹配）；纯函数 `validate_node_name`/`build_node_path`/`compute_attachments`（/a 不误匹配 /ab）；REST `GET /tree` / `POST /tree/nodes` / `GET /tree/subtree` / `POST /tree/assign|unassign`；MCP `tree_view`/`tree_add`/`tree_assign`（工具 28 → 31）；父缺失/重名/非法名一律 422（宁 miss 不脏写）。票据 01
- **技能结晶（v0.7，借鉴 aiduMEI SkillCrystallizer 窄版）**: `lantai/services/crystal_service.py`——检测复用 autodream 聚类（同 lane + 共享关键词，min_size=3，排除 general/chat 噪声 lane），簇 → `SkillCrystal` candidate（procedure 只记摘要不塞全文）；Mímir 铁律：只产候选，人工裁决 `POST /crystals/{id}/decide` approve 必须带非空 steps（宁 miss 不脏写）→ 落成 Skill 资产（复用 create_skill），reject → archived + reason；幂等 upsert（skill_name 冲突 hit_count+1）；settings `CRYSTAL_*` 三项；REST `/crystals` + `/crystals/detect`；MCP `crystals_list`/`crystals_detect`/`crystal_decide`（工具 31 → 34）。票据 02
- **记忆 Wiki（ADR-0017，借鉴 TencentDB Agent Memory LLM-Wiki ingest-v2 窄版）**: `lantai/services/wiki_service.py`——场景/技能 → `docs/memory-wiki/` 页面（frontmatter + 成员 + 相关场景 `[[wikilink]]`）+ `index.md`（按类型分组稳定索引）+ `overview.md` 综述（LLM 优先，失败/关闭确定性兜底）；`run_wiki_update_once` 幂等增量维护（过期页自动清理）；`mem_sync` 升级为 scene+digest+wiki 三件套；CLI `scripts/run_wiki.py`（--no-llm/--json）；MCP `wiki_read` 下钻（工具 20 → 21）；settings 新增 `WIKI_*` 六项；`tests/test_wiki.py` 11 例（纯函数不 mock + 真实 SQLite/tmp_path 集成）
- **上下文卸载（ADR-0016，借鉴 TencentDB Agent Memory offload_server/compact 窄版）**: `lantai/services/offload_service.py`——超长记忆（`SHELL_HOOK_OFFLOAD_CHARS` 默认 2000）全文落 `docs/memory-offload/{memory_id}.md`（`OFFLOAD_OUTPUT_DIR` 可覆盖），Shell Hook 上下文只注入「摘要 + 全文路径」行，需要时经 MCP `offload_read` 取回完整原文（白名单文件名 + 目录内路径校验防穿越）；落盘失败静默降级为截断注入，截断指南附 offload_read 提示；MCP 工具 19 → 20；`tests/test_offload.py` 8 例（纯函数不 mock + 真实 tmp_path/SQLite 集成）
- **中文命名体系（ADR-0013）**: 正式名「有出处、有意义、有登记」——命名层级 L0–L4 + 三大意象源（官职/典籍/器物）+ 功能域映射表（候选意象：直书/拾遗/佐证/更漏/参商/校雠/底本/拟议/起居注/卷宗/法门/三省/测候/目次/尘封）；新名称必须先登记 `CONTEXT.md` 词汇表；AGENTS.md 新增命名纪律
- **MCP 客户端矩阵（多客户端接入合规）**: `docs/mcp-client-matrix.md`——Claude Code / Cursor / Gemini CLI / Codex / Hermes 五端接入指南 + 15 工具清单 + 每端验证清单（tools 元数据 / description / inputSchema / ping+initialized 通知 / tools.call 缺参 -32602）；`tests/test_mcp.py` 追加 3 条标准合规测试
- **检索透明（supersedes explain 降权标记）**: `hybrid.py::_apply_supersedes_order` 新增 `breakdowns` 参数——explain 记录 `superseded_by`（新值 id 列表）+ `demoted: True`，向量主路径 / rerank / FTS 兜底三处调用点统一接入；修复 superseded_by 误记分数的 bug（改用 `superseder_ids`）；`tests/test_fts_integration.py::test_supersedes_explain_marks_demotion` 端到端断言
- **autodream 蒸馏（后台记忆合成 → 待审提案）**: `lantai/evolution/autodream.py`——同 lane + 共享关键词贪心聚类（确定性、min_size 过滤），`plan_distillation` 新值在前 + 去重 + 置信度随簇大小递增（0.5 + 0.15*(n-1)），`run_autodream_once` dry-run 或落 pending 提案（低置信度进 skipped，宁 miss 不脏写）；`scripts/run_autodream.py` CLI；settings 新增 `AUTODREAM_ENABLED` / `AUTODREAM_MIN_CLUSTER` / `AUTODREAM_MAX_DAILY` / `AUTODREAM_MIN_CONFIDENCE`；4 个不 mock 冒烟测试
- **记忆概览 CLI（只读聚合，一眼看清现状）**: `lantai/ops/overview.py::build_overview/get_overview`——记忆总数 / active / archived 按 lane 与 decay_class 分布、待审候选（pending_review）积压、检查点版本数、待审提案数；`scripts/memory_overview.py` Markdown / JSON 双输出；`tests/test_overview.py` 真实临时库 3 例（不 mock 聚合逻辑）

### Added
- **lane 级 ACL（按 agent_id 绑定 lane 集，借鉴 TencentDB Memory Hub Fixed Binding 窄版）**: `lantai/core/acl.py`——`allowed_lanes` / `lane_allowed` / `filter_results_by_lanes` 纯函数 + `verify_agent` FastAPI 依赖；settings `AGENT_LANE_BINDINGS`（空 = 不启用，默认关闭零行为变化）；启用后受保护端点强制 `X-Agent-Id` 且已绑定（缺失/未绑定 403），`POST /search` 结果按绑定 lane 收窄（兼容 memory.lane 与 FTS 兜底两形态，宁 miss 不放行），`POST /add` / `POST /add/raw` 越界 lane 拒绝落库。测试 `tests/test_acl.py`（7 例，纯函数不 mock + 路由 403/过滤接线），票据 08
- **冷启动导入（历史会话 JSONL 批量原文直存，借鉴 TencentDB Agent Memory 冷启动导入）**: `lantai/services/import_service.py`——`parse_import_lines` 纯函数逐行解析（content 必填，created_at/updated_at ISO8601 保留原始时间戳，lane/tags 可选，非法行记 {line, reason} 不静默修正）；verbatim 直存（sha256 幂等去重，embedding/向量索引失败不阻断落库，FTS 可检索）；REST `POST /import/jsonl`（受保护，空文本 422）+ `scripts/import_jsonl.py` CLI。测试 `tests/test_import_jsonl.py`（6 例，纯函数不 mock + 真实临时 SQLite 仅 mock 外部依赖），票据 07
- **冷启动导入·对话链（历史会话 JSONL → 摄取链 + 时间戳继承，借鉴 TencentDB Agent Memory L0 + v2.0.1 时间戳修正）**: `lantai/ingestion/import_service.py`——L0 会话格式（{role, content, timestamp[, session]}）经 `scripts/run_import.py` 批量喂既有对话摄取链；`ingest_dialogue(created_at=...)` 透传原始时间戳（RawDocument.fetched_at / MemoryCandidate.created_at），provenance.prompt=dialogue-session-import，promoter 按 import provenance 把 created_at 继承到 MemoryItem（时间线不压平；非导入路径不覆盖）；--dry-run 零写库预览，`IMPORT_MAX_LINES=5000` 防护。测试 `tests/test_import.py`（8 例，纯函数不 mock + 真实 SQLite/tmp_path + 演化链对照），票据 01，ADR-0018
- **VAULT 档案控制台（锦囊队列 + 档案浏览 + 衰减概览，借鉴 aiduMEI v18.2 控制台）**: `lantai/services/memory_service.py::build_memories_page`（纯函数，只读分页 limit∈[1,100]/offset，lane/status/decay_class/memory_type 过滤，updated_at 新→旧 + id 稳定排序，content 截断带省略号）+ `list_memories`；REST `GET /memories`（受保护）；`/ui/vault` 零依赖静态页——总览卡片（总数/active/archived/待审锦囊）、锦囊待审队列（页内采纳/驳回裁决 `POST /candidates/{id}/review`）、档案表格（过滤 + 分页）、衰减概览（by_decay_class/by_lane 条形图，`/stats` 新增 by_decay_class 聚合）；`/ui` 入口页第三个面板。测试 `tests/test_vault_panel.py`（5 例，纯函数不 mock 直调真实临时 SQLite），票据 06
- **EVOLVE 检索质量看板（借鉴 aiduMEI v18.2 控制台）**: `lantai/observability/recall_report.py::recent_retrieval_events`（最近 N 条检索事件，新→旧，limit∈[1,100]）；REST `GET /retrieval/recent-events`；`/ui/evolve` 零依赖静态页——总览卡片（真实查询/零召回率/token 粗估/场景命中率）+ 按 lane/意图分布条形图 + 事件流表格；`/ui` 改为双面板入口页。测试 `tests/test_evolve_panel.py`（5 例），票据 05
- **追忆漏斗控制台（RECALL 面板，借鉴 aiduMEI v18.2 控制台）**: `lantai/api/routes_ui.py`——零依赖静态页（内联 CSS/JS，无 node/打包），`GET /ui/recall` 公开托管，页内调 `POST /search?trace=true` 渲染 意图→向量→衰减→(重排)→最终 的召回漏斗（每步耗时/候选数/分数区间）+ 闸门裁决 + 结果列表；API Key 可选（localStorage）；`/ui` 307 重定向。测试 `tests/test_ui_recall.py`（2 例冒烟），票据 04
- **Obsidian 双链 + verbatim 专用检索（Ticket 02，借鉴 aiduMEI v18.3）**: `lantai/services/obsidian_service.py`——`extract_wikilinks()` 纯函数解析 `[[页面]]`/`[[页面|别名]]`（忽略 `[[#锚点]]`）；`sync_obsidian_note()` 笔记原文零 LLM 直存（复用 P0-1 `add_raw_memory`，content_hash 幂等），双链词与笔记标题沉淀为实体（`memory_type="entity"`，不建索引不参与召回）并建 `MemoryEdge(relation="links")`，重复推送实体/边幂等；REST `POST /obsidian/sync` + `GET /verbatim/search`（专用通道）；settings `VERBATIM_IN_RECALL`（默认 false，verbatim 不进混合召回，hybrid 向量/FTS 兜底双路径过滤）；MCP `obsidian_sync`。测试见 `tests/test_verbatim_obsidian.py`（5 例不 mock 冒烟）
- **provenance 提取来源（记忆可溯源，借鉴 TencentDB Agent Memory Roadmap）**: `lantai/core/provenance.py::make_provenance` 记录「哪套 prompt / 哪个模型 / 何时产出」；`MemoryCandidate` / `MemoryProposal` / `MemoryItem` 补 `provenance` JSON 列（`user_version` 5→6 增量迁移，老库零丢失）；四个提取入口统一填充（LLM 提取/论文 → extract-v1、memory fastpath → fastpath-direct、dialogue fastpath/闲聊 → dialogue-fastpath/dialogue-chitchat）；proposer → promoter 链路继承同源，最终记忆可回答"谁产出的"；记忆概览新增 `provenance_by_prompt` 分布。决策见 [ADR-0015](docs/adr/0015-provenance.md)
- **mem: 会话指令（MCP 命令式维护，借鉴 TencentDB Agent Memory mem-command）**: `lantai/services/mem_command.py`——`mem_help`（命令表纯函数）/ `mem_sync`（scene 增量聚类补跑 + 今日 digest 重算，子步骤异常不阻断）/ `mem_create_skill`（零 LLM 结构化落库：memory_type="skill" + structure.steps + decay_class="procedural"，sha256 幂等去重，进向量+FTS 可被 `## Skill` 块注入）；MCP 新增 `mem_help` / `mem_sync` / `mem_create_skill` 三个工具（15→18）；校验失败 -32602（宁 miss 不脏写）。决策见 [ADR-0014](docs/adr/0014-mem-command.md)
- **scene 增量聚类（ADR-0012 后续，借鉴 TencentDB Agent Memory L2 场景层）**: `MemoryScene.centroid` 质心落库（`user_version` 4→5 增量迁移，老库零丢失）；`rebuild_scenes` 构建时同步落质心（`_mean_vector`）；纯函数 `incremental_cluster`（复用 `cosine_sim`，cosine ≥ `SCENE_CLUSTER_THRESHOLD` 并入最相似场景，未命中保持无 scene_id——宁 miss 不脏写）；`assign_new_memory` 新记忆并入既有场景并刷 heat/member_count（零写放大）；消化期 `run_evolve_once` 末尾自动补跑（`SCENE_LAYER_ENABLED` 门控）+ `POST /scenes/assign` 手动入口
- **零召回率监控 + token 成本估算（可观测性，借鉴 TencentDB Agent Memory）**: `RetrievalEvent` 补 `scene_ids` / `estimated_tokens`（`user_version` 3→4 迁移）；`lantai/observability/recall_report.py` 提供 `estimate_tokens`（CJK 按字、其余 4 字符/词元，零依赖粗估）与 `recall_report(days)` 窗口聚合——排除系统噪音的零召回率、按 lane/intent 分组、场景命中率（配合 scene 层）、token 总量/均值；`log_retrieval` 埋点落 scene_ids（去重）与 token 估算；入口 REST `GET /retrieval/recall-report` + MCP `recall_report`；窗口默认 `RECALL_MONITOR_WINDOW_DAYS=7`
- **scene 聚合层（ADR-0012，借鉴 TencentDB Agent Memory L2 场景层）**: `MemoryScene` 表 + `MemoryItem.scene_id`（`user_version` 2→3 增量迁移，老库零丢失）；`scene_service` 确定性 embedding 聚类（cosine ≥ `SCENE_CLUSTER_THRESHOLD`，单成员簇不建场景）＋ LLM 批量命名/摘要（失败降级代表 key，宁 miss 不脏写）；`POST /scenes/rebuild` 幂等全量重建，heat = 成员 `use_count` 求和（零写放大）；shell_hook `build_context` 命中场景成员时导航块优先注入（`## Scene: 名称（热度 N，成员 M）` + 摘要 + 成员 key，渐进式披露），详情用 MCP `scene_get` / REST `GET /scenes/{id}` 下钻，`scenes_list` 浏览；`SCENE_LAYER_ENABLED` 默认关
- **Schema 版本化迁移（v0.6 Ticket 01，借鉴 aiduMEI v18.3 Fast-Update）**: `lantai/storage/db.py` 引入 `PRAGMA user_version` 增量迁移链——`CURRENT_SCHEMA_VERSION=2` + `apply_migrations()` + `_ensure_column()`，把原有手写幂等 ALTER（memoryitem.decay_class / retrieval_event.is_system_noise / memorycandidate.review_due_at）收口为版本化流程；老库自动基线 v1→v2，异常只记日志不阻断启动；`tests/test_migrations.py` 5 例不 mock 冒烟测试（空库/全新库幂等/缺列老库补齐+数据零丢失/重复启动 no-op/预版本化库）
- **遗忘质量离线门禁（CI / 发布自证）**: `lantai/eval/offline.py::run_offline_eval`——临时 SQLite + 真实 FTS5 建表 + 仅 mock 外部依赖（embedding / 向量存储 / 意图 LLM），真实执行 种子→遗忘→检索→指标→清理；`check_gates` 断言五维门槛（stale=0 / typo=1 / fresh=1 / temporal=1 / superseded=1），残留只报告不设门槛（诚实测量）；`scripts/run_forgetting_quality.py --check` 门禁模式 FAIL 退出码 1，可直接挂 CI
- **中文记忆评测集 v1 发布稿**: `docs/memory-quality/chinese-memory-v1.md`——评测集规格（13 case / 命名空间隔离 / trigram 词边界约束）、六维指标定义、实测结果、两条复现命令、诚实原则与边界；对外主张依据（英文生态无中文基准且分数不可复现）
- **supersedes 边感知排序（遗忘质量回归）**: `hybrid.py::_apply_supersedes_order` 在打分后降权被取代旧值（新值同在候选集时压到新值之下，新值缺席不动旧值——宁 miss 不脏写，残留如实测量）；向量主路径 / rerank 分支 / FTS 兜底路径统一接入；settings 新增 `SUPERSEDES_ORDERING_ENABLED` / `SUPERSEDES_DEMOTE_EPSILON`；评测集 `superseded_order_accuracy` 由 0.5 确定性升至 1.0，端到端断言升级
- **遗忘质量自测体系（一年内档）**: `lantai/eval/forgetting_quality.py` 六项维度化指标（陈旧残留/错别字容错/对照召回/时效排序/取代排序/取代残留），真实 DB 种子→真实遗忘→真实检索（FTS 兜底确定性），finally 清理含 supersedes 边；`lantai/eval/chinese_memory_cases.py` 中文评测集 v1（13 case：typo×4/fresh×3/stale×2/temporal×2/superseded×2，全部查询经 sqlite 直连验证 FTS 可命中）；`scripts/run_forgetting_quality.py` CLI 落盘报告；首份报告 `docs/memory-quality/2026-08-11.md`——typo/fresh/temporal 全绿、stale 零残留、superseded 暴露真实缺口（FTS 兜底下检索无 supersedes 排序语义）
- **Shell Hook 召回预算 + 记忆工具指南（借鉴 TencentDB Agent Memory）**: `shell_hook.py` 新增码点安全截断 `_truncate_codepoints`、总预算分配 `_apply_recall_budget`、指南生成 `_build_tools_guide`——单条记忆注入上限 `SHELL_HOOK_MAX_CHARS_PER_MEMORY=200`（替代硬编码 `[:200]`）+ 总预算 `SHELL_HOOK_MAX_TOTAL_CHARS=1500`，超预算截断/丢弃并附后缀提示；有命中时注入末尾附「记忆使用指南」（何时深挖、每轮最多检索 3 次、add 回写），`SHELL_HOOK_TOOLS_GUIDE` 可关；evidence 与注入行同源截断保持一致。决策见 [ADR-0006](docs/adr/0006-shell-hook-contract.md)，调研见 `docs/research/tencentdb-agent-memory-borrow.md`
- **Skill 资产化（借鉴 TencentDB Agent Memory）**: `proposer` 把候选 `actions` 沉淀为 `proposed_patch["structure"]`（name/description/steps），`promoter` 落库到 `MemoryItem.structure`，steps 非空强制 `decay_class="procedural"`（永不衰减铁律天然浮顶）；Shell Hook 对 procedural 记忆注入 Skill 块（`## Skill: 名称` + 描述 + 编号步骤），普通记忆保持平铺，同样受召回双预算约束。决策见 [ADR-0011](docs/adr/0011-skill-asset.md)
- **Raw Drawer 原文直存（P0-1）**: `POST /add/raw`——verbatim 记忆零 LLM 直写（只 embedding + FTS5），内容 sha256 幂等去重，不走提取/闸门/演化；MCP `raw_add` 工具。决策见 [ADR-0009](docs/adr/0009-raw-drawer-verbatim.md)
- **冲突消解确定性层（P0-2）**: `gate/conflict_rules.py` 互斥规则集（settings 可配）优先、LLM 回落双通道；规则命中写 `ConflictEvent` 账本（可溯源、可裁决）；REST `GET /conflicts` + `POST /conflicts/{id}/resolve`，MCP `conflicts_list` / `conflict_resolve`；闸门决策语义不变（仍走待审队列人工裁决）。决策见 [ADR-0010](docs/adr/0010-conflict-resolution-layer.md)
- **MCP 工具扩容（第一批）**: 8 → 12 工具，新增 `raw_add` / `rollback` / `conflicts_list` / `conflict_resolve`
- **Hermes 插件对话自动写入（v0.5 落地）**: 插件源码纳入仓库 `hermes-plugin/remembrance-hook/`（版本化+可测试）——`pre_llm_call` 把 user_message 累积到会话缓冲（有界防膨胀），`on_session_end` 每轮对话结束 flush 给 `shell_hook --serve` 新 dialogue 通道 → `ingest_dialogue`（fastpath 直通/提取建候选/闲聊入待审队列）；`scripts/install_hermes_plugin.py` 一键部署（自动备份旧版不删除）；settings 新增 `SHELL_HOOK_DIALOGUE_TIMEOUT=30`（LLM 提取超时）
- **Hermes 会话钩子验证（research）**: 确认 Hermes 插件 API 存在 `on_session_end`（每轮对话结束触发，桌面版与 CLI 通用，payload 无消息文本）——推荐实现：插件缓冲 `pre_llm_call` 的 user_message + `on_session_end` flush 给 `ingest_dialogue`（Supermemory 同款模式）；备选 state.db 只读扫描（sessions/messages 表 + WAL 安全，增量游标 last_activity_at）已探明 schema；结论见 `.scratch/dialogue-loop/issues/05`，已回写 spec
- **Search Transparency（检索透明）**: `remembrance/retrieval/evidence.py::build_evidence`（检索结果 → 来源说明 id+摘要+分数，rerank 路径按内容反查 id）——shell_hook `build_context` 注入附「本次依据」段（记忆 id + 摘要，有命中时）+ 结构化 `evidence` 字段；MCP `search` 与 REST `POST /search` 响应补 `evidence`；无命中/异常零侵入降级
- **Dialogue Ingest（对话写通道）**: `remembrance/ingestion/dialogue.py::ingest_dialogue`——对话文本 → 现有提取链（rawdocument→memorycandidate，不新建存储）：fastpath 白名单直通（记住/自我声明/偏好）；闲聊（过短/社交结束语）进待审队列；LLM 提取低置信度/失败（上游 502）兜底入队不丢数据；lane 启发式预判（preference/fact/general）。REST `POST /dialogue`（routes_dialogue.py）+ MCP `add_dialogue`；settings 新增 `DIALOGUE_ENABLED` / `DIALOGUE_MIN_CHARS` / `DIALOGUE_MIN_EXTRACTOR_CONF`（零硬编码，对话通道专用阈值不受 .env GATE_* 覆盖影响）
- **Candidate Review Queue（候选可见队列）**: `memorycandidate.review_due_at` 字段 + `pending_review` 状态——gate REJECT 不再静默丢弃（evolve_worker 落队，TTL `CANDIDATE_TTL_DAYS=7` 自动归档）；`remembrance/services/candidate_service.py`（enqueue_rejected / list_pending_candidates / review_candidate / run_candidate_ttl_once）；REST `GET /candidates/pending` + `POST /candidates/{id}/review`（approve→提案链并应用 / reject→归档）；MCP `candidates_pending` / `candidate_review`；每日 TTL 任务 `run_candidate_ttl`（digest_worker.py，`CANDIDATE_TTL_CRON_HOURS=24`）；幂等列迁移
- **Retrieval noise filtering**: `RetrievalEvent.is_system_noise` field + `is_system_noise()` classifier (deterministic prefixes + length gap), `scripts/mark_retrieval_noise.py` for idempotent backfill of legacy events
- **Hermes desktop injection plugin**: `remembrance-hook` Python plugin registering `pre_llm_call` (serve mode runs no shell hooks — `_AGENT_COMMANDS` excludes `serve`); resident `shell_hook.py --serve` NDJSON loop eliminates cold-start cost
- **Hermes onboarding scripts**: `scripts/migrate_home.py` (safe REMEMBRANCE_HOME migration), `scripts/verify_remembrance.py` (8-point self-check), `docs/hermes-install-handoff.md`
- **Manual call guide**: `docs/remembrance-manual-call.md` — Hermes chat / CLI JSON-RPC / REST API entry points
- **Dry-run evaluation pipeline**: `remembrance/eval/` — `EvalQuerySet`/`EvalRun` tables, `build_query_set()`, `compute_metrics()` (zero_result / avg_result_count / jaccard / weak_hit_rate), `run_dry_run()` with `param_overrides` + `intent_mode`, `scripts/run_dry_run.py` CLI; first report `docs/dry-run-report-v1.md` (179 samples, zero_result 0.0%)
- **Step 7 shadow observation**: `ShadowWindow` table + `shadow.py` decision logic (evaluate_window 3-guardrail: zero_result/avg_result/jaccard; conservative hold) + `runtime.py` integration (open_shadow with MAX_ACTIVE_SHADOW_WINDOWS guard, check_shadow_due periodic dry-run comparison, rollback_snapshot guardrail). DEDUP shadow-only (shadow params never write ParamOverride), manual gate preserved (promote marks only, application stays human-approved)
- **Step 8 verification feedback**: `SignalReliabilityStat` table (venue_class-level pass/fail/fail_streak) + `reliability.py` (record_verification_result, reliability_penalty with PENALTY_* thresholds, apply_penalty_to_weight) + `resolve_gating` venue_class hook — penalty only lowers weight (只降不升), TTL expiry restores, manual gate unchanged

### Fixed
- **全量顺序测试污染（调度器线程泄漏）**: 11 个测试文件经 `from api_server import app` + TestClient 触发 lifespan，会启动真实 BackgroundScheduler（evolve/ingest/forget 等 worker 对真实库做真实 LLM 调用——拖慢全量、写脏真实库），且 `stop_scheduler(wait=False)` 不等待在跑任务留下僵尸线程——`tests/conftest.py` 新增 autouse fixture 置空 `api_server.start_scheduler`，测试进程内永不启动真实调度器（零生产代码改动）。排查见 `.scratch/v0.6-aidumei-absorb/issues/03-fullrun-scheduler-pollution.md`
- **FTS5 短词毒化 AND 链**: `search_fts` 剔除 <3 字符 token（trigram 最小成词长度）——2 字词（如「密钥」）在索引侧无法成词，却让整条 `"API" AND "密钥"` 查询整体失效（评测集 superseded 用例暴露）；短词在 trigram 下本就零命中，剔除不改变任何既有命中结果
- **UTF-8 stdin corruption**: force `sys.stdin/stdout.reconfigure(encoding="utf-8")` in `mcp_server.py` and `shell_hook.py` — Windows GBK decoding turned Chinese queries into mojibake (「你好」→「浣犲ソ」) causing zero-recall + `no_signal`
- **Hermes shell-hook interpreter**: hooks config now points to `.venv-audit` python (hermes venv lacked sqlmodel); serve mode uses plugin channel instead
- **shell_hook timeout semantics**: single-shot mode returns `{}` on timeout instead of `os._exit` (serve mode needs resilience)

### Added
- **Raw Drawer 原文直存（P0-1）**: `POST /add/raw`——verbatim 记忆零 LLM 直写（只 embedding + FTS5），内容 sha256 幂等去重，不走提取/闸门/演化；MCP `raw_add` 工具。决策见 [ADR-0009](docs/adr/0009-raw-drawer-verbatim.md)
- **冲突消解确定性层（P0-2）**: `gate/conflict_rules.py` 互斥规则集（settings 可配）优先、LLM 回落双通道；规则命中写 `ConflictEvent` 账本（可溯源、可裁决）；REST `GET /conflicts` + `POST /conflicts/{id}/resolve`，MCP `conflicts_list` / `conflict_resolve`；闸门决策语义不变（仍走待审队列人工裁决）。决策见 [ADR-0010](docs/adr/0010-conflict-resolution-layer.md)
- **MCP 工具扩容（第一批）**: 8 → 12 工具，新增 `raw_add` / `rollback` / `conflicts_list` / `conflict_resolve`
- **used_ids weak-label backfill channel (direction-2)**: `POST /retrieval/backfill` REST route (`routes_retrieval.py`) + MCP `backfill` tool + `event_id` surfaced in `search` responses (REST + MCP + shell_hook). Generation side (Hermes) records which memories actually went into an answer → `backfill_used_ids()` → dry-run `weak_hit_rate` goes live. `run_dry_run` now loads `used_ids_map` by event_id (honest `None` when no backfill data)
- **Position-sensitive param-matrix analysis**: `scripts/run_param_matrix.py` — batch dry-run across weight tuples + top1/top3 consistency / position-drift metrics (Jaccard set-blindness fix); report `docs/param-matrix-report.md` (empirical: W_VECTOR 0.6→0.75 shifts top1 on 14/179 queries)
- **Step 8 人工验证入口**: POST /verification REST 路由（记录人工验证结果）+ GET /verification/stats（列出各信号类别可靠性统计与当前降权系数）——
ecord_verification_result 此前仅有函数无入口，现闭环打通
- **Backfill channel self-check**: `scripts/verify_backfill.py` — 8-point verification (MCP backfill tool registered / search returns event_id / handler / table+column / real write-read / `_load_used_ids_map` / production fill rate); guide `docs/used-ids-backfill-guide.md` updated with self-check usage

### Fixed
- **FTS5 短词毒化 AND 链**: `search_fts` 剔除 <3 字符 token（trigram 最小成词长度）——2 字词（如「密钥」）在索引侧无法成词，却让整条 `"API" AND "密钥"` 查询整体失效（评测集 superseded 用例暴露）；短词在 trigram 下本就零命中，剔除不改变任何既有命中结果
- **FTS5 MATCH 特殊字符语法错误**: search_fts 此前把原始查询直接拼进 FTS5 MATCH（AND.join(split)），含 = @ . ? / 的查询触发 syntax error 使整条 FTS 通道降级（真实查询大量触发）；现逐词引号包裹 + 双引号转义，trigram 子串语义不变（实测矩阵 1284 次检索警告 0）
- **e2e 测试外部网络 mock 补齐**: 	est_e2e.py 此前未 mock 提取器 chat_json 与 mbed（外部 LLM/embedding API），上游网络慢时每条用例拖 20-30s 甚至卡死——已按测试纪律补 mock（仅外部网络，业务逻辑真实执行）: Edit/Write to Windows-mounted files could drop trailing bytes (null-fill) — use bash + Python writes for mounted-path edits

### Changed
- **项目中文名定为「兰台记忆（Lantai）」**: 取自汉代皇家档案馆「兰台」——为 AI 保存、检索、演化、遗忘长期记忆的档案库；英文代号定为 Lantai。待审候选队列（`pending_review`）别名定为「锦囊」
- **内部包名统一为 lantai**: Python 包 `remembrance/` → `lantai/`（全库导入路径同步）；pip 包名 `remembrance-system` → `lantai`；环境变量 `REMEMBRANCE_HOME` 更名 `LANTAI_HOME`（旧名兼容回退）；MCP serverInfo 更名 lantai；Docker 镜像标签与文档路径同步。数据文件（remembrance.db / .chromadb）保留不变
- **Hermes 插件更名 lantai-hook**: hermes-plugin/remembrance-hook/ → lantai-hook/（manifest、日志前缀、部署脚本、测试、文档同步）；已重装到 Hermes 并清理旧插件目录

## [0.3.7] - 2026-08-04

### Fixed
- **FTS5 短词毒化 AND 链**: `search_fts` 剔除 <3 字符 token（trigram 最小成词长度）——2 字词（如「密钥」）在索引侧无法成词，却让整条 `"API" AND "密钥"` 查询整体失效（评测集 superseded 用例暴露）；短词在 trigram 下本就零命中，剔除不改变任何既有命中结果
- **Data loss fix**: `apply_proposal` now accepts `APPROVED` status — human approval and `run_pending` paths were previously broken (found in live deployment)
- **SQLite self-deadlock**: Use outer session for `MemoryEdge` in `apply_proposal` — nested session caused deadlocks under concurrent writes (found in live deployment)
- **Gate threshold isolation**: Pin `GATE_MIN` in test to isolate from host `.env` pollution

### Changed
- Untrack `.workbuddy` session metadata (keep on disk), keep parallel-session prompt doc in `docs/`

### Removed
- Root-level empty `remembrance__init__.py` (0-byte junk re-added in previous commit)
- P2 plan (tidal-coalescing + MCP) — superseded by v0.3.1/v0.3.3 implementations
- Accidentally removed `docs/plans/` restored

## [0.3.6] - 2026-07-31

### Added
- **Raw Drawer 原文直存（P0-1）**: `POST /add/raw`——verbatim 记忆零 LLM 直写（只 embedding + FTS5），内容 sha256 幂等去重，不走提取/闸门/演化；MCP `raw_add` 工具。决策见 [ADR-0009](docs/adr/0009-raw-drawer-verbatim.md)
- **冲突消解确定性层（P0-2）**: `gate/conflict_rules.py` 互斥规则集（settings 可配）优先、LLM 回落双通道；规则命中写 `ConflictEvent` 账本（可溯源、可裁决）；REST `GET /conflicts` + `POST /conflicts/{id}/resolve`，MCP `conflicts_list` / `conflict_resolve`；闸门决策语义不变（仍走待审队列人工裁决）。决策见 [ADR-0010](docs/adr/0010-conflict-resolution-layer.md)
- **MCP 工具扩容（第一批）**: 8 → 12 工具，新增 `raw_add` / `rollback` / `conflicts_list` / `conflict_resolve`
- Comprehensive README with architecture diagram, features table, quickstart, API reference, and testing guide
- README rewritten in aiduMEM style (with adaptation credit)
- MIT LICENSE

### Fixed
- **FTS5 短词毒化 AND 链**: `search_fts` 剔除 <3 字符 token（trigram 最小成词长度）——2 字词（如「密钥」）在索引侧无法成词，却让整条 `"API" AND "密钥"` 查询整体失效（评测集 superseded 用例暴露）；短词在 trigram 下本就零命中，剔除不改变任何既有命中结果
- Removed empty `remembrance__init__.py` from root

## [0.3.5] - 2026-07-28

### Added
- **Raw Drawer 原文直存（P0-1）**: `POST /add/raw`——verbatim 记忆零 LLM 直写（只 embedding + FTS5），内容 sha256 幂等去重，不走提取/闸门/演化；MCP `raw_add` 工具。决策见 [ADR-0009](docs/adr/0009-raw-drawer-verbatim.md)
- **冲突消解确定性层（P0-2）**: `gate/conflict_rules.py` 互斥规则集（settings 可配）优先、LLM 回落双通道；规则命中写 `ConflictEvent` 账本（可溯源、可裁决）；REST `GET /conflicts` + `POST /conflicts/{id}/resolve`，MCP `conflicts_list` / `conflict_resolve`；闸门决策语义不变（仍走待审队列人工裁决）。决策见 [ADR-0010](docs/adr/0010-conflict-resolution-layer.md)
- **MCP 工具扩容（第一批）**: 8 → 12 工具，新增 `raw_add` / `rollback` / `conflicts_list` / `conflict_resolve`
- Test suite: 120 tests, all green
  - FTS5 integration tests
  - SSRF safety tests
  - Backup/recovery tests
  - MCP protocol tests
  - Shell Hook timeout tests

### Security
- Supply chain hardening: GitHub Actions pinned to commit SHA (not mutable tags)
- Docker images run as non-root

## [0.3.4] - 2026-07-25

### Added
- **Raw Drawer 原文直存（P0-1）**: `POST /add/raw`——verbatim 记忆零 LLM 直写（只 embedding + FTS5），内容 sha256 幂等去重，不走提取/闸门/演化；MCP `raw_add` 工具。决策见 [ADR-0009](docs/adr/0009-raw-drawer-verbatim.md)
- **冲突消解确定性层（P0-2）**: `gate/conflict_rules.py` 互斥规则集（settings 可配）优先、LLM 回落双通道；规则命中写 `ConflictEvent` 账本（可溯源、可裁决）；REST `GET /conflicts` + `POST /conflicts/{id}/resolve`，MCP `conflicts_list` / `conflict_resolve`；闸门决策语义不变（仍走待审队列人工裁决）。决策见 [ADR-0010](docs/adr/0010-conflict-resolution-layer.md)
- **MCP 工具扩容（第一批）**: 8 → 12 工具，新增 `raw_add` / `rollback` / `conflicts_list` / `conflict_resolve`
- FTS5 trigram parallel recall + BM25 caching ([ADR-0008](docs/adr/0008-fts5-parallel-recall.md))

## [0.3.3] - 2026-07-22

### Added
- **Raw Drawer 原文直存（P0-1）**: `POST /add/raw`——verbatim 记忆零 LLM 直写（只 embedding + FTS5），内容 sha256 幂等去重，不走提取/闸门/演化；MCP `raw_add` 工具。决策见 [ADR-0009](docs/adr/0009-raw-drawer-verbatim.md)
- **冲突消解确定性层（P0-2）**: `gate/conflict_rules.py` 互斥规则集（settings 可配）优先、LLM 回落双通道；规则命中写 `ConflictEvent` 账本（可溯源、可裁决）；REST `GET /conflicts` + `POST /conflicts/{id}/resolve`，MCP `conflicts_list` / `conflict_resolve`；闸门决策语义不变（仍走待审队列人工裁决）。决策见 [ADR-0010](docs/adr/0010-conflict-resolution-layer.md)
- **MCP 工具扩容（第一批）**: 8 → 12 工具，新增 `raw_add` / `rollback` / `conflicts_list` / `conflict_resolve`
- SSRF hardening: external fetch protocol whitelist + DNS resolution IP blocking
- Atomic backup/recovery with online backup + manifest SHA256 validation
- MCP server: input validation + exception isolation

## [0.3.2] - 2026-07-18

### Added
- **Raw Drawer 原文直存（P0-1）**: `POST /add/raw`——verbatim 记忆零 LLM 直写（只 embedding + FTS5），内容 sha256 幂等去重，不走提取/闸门/演化；MCP `raw_add` 工具。决策见 [ADR-0009](docs/adr/0009-raw-drawer-verbatim.md)
- **冲突消解确定性层（P0-2）**: `gate/conflict_rules.py` 互斥规则集（settings 可配）优先、LLM 回落双通道；规则命中写 `ConflictEvent` 账本（可溯源、可裁决）；REST `GET /conflicts` + `POST /conflicts/{id}/resolve`，MCP `conflicts_list` / `conflict_resolve`；闸门决策语义不变（仍走待审队列人工裁决）。决策见 [ADR-0010](docs/adr/0010-conflict-resolution-layer.md)
- **MCP 工具扩容（第一批）**: 8 → 12 工具，新增 `raw_add` / `rollback` / `conflicts_list` / `conflict_resolve`
- FTS5 schema + Chronos timezone + BM25 compatibility fixes

## [0.3.1] - 2026-07-15

### Added
- **Raw Drawer 原文直存（P0-1）**: `POST /add/raw`——verbatim 记忆零 LLM 直写（只 embedding + FTS5），内容 sha256 幂等去重，不走提取/闸门/演化；MCP `raw_add` 工具。决策见 [ADR-0009](docs/adr/0009-raw-drawer-verbatim.md)
- **冲突消解确定性层（P0-2）**: `gate/conflict_rules.py` 互斥规则集（settings 可配）优先、LLM 回落双通道；规则命中写 `ConflictEvent` 账本（可溯源、可裁决）；REST `GET /conflicts` + `POST /conflicts/{id}/resolve`，MCP `conflicts_list` / `conflict_resolve`；闸门决策语义不变（仍走待审队列人工裁决）。决策见 [ADR-0010](docs/adr/0010-conflict-resolution-layer.md)
- **MCP 工具扩容（第一批）**: 8 → 12 工具，新增 `raw_add` / `rollback` / `conflicts_list` / `conflict_resolve`
- P0 audit remediation:
  - Repository hygiene
  - Binding authentication enforcement
  - Test baseline establishment

## [0.1.0] - 2026-06-20

### Added
- **Raw Drawer 原文直存（P0-1）**: `POST /add/raw`——verbatim 记忆零 LLM 直写（只 embedding + FTS5），内容 sha256 幂等去重，不走提取/闸门/演化；MCP `raw_add` 工具。决策见 [ADR-0009](docs/adr/0009-raw-drawer-verbatim.md)
- **冲突消解确定性层（P0-2）**: `gate/conflict_rules.py` 互斥规则集（settings 可配）优先、LLM 回落双通道；规则命中写 `ConflictEvent` 账本（可溯源、可裁决）；REST `GET /conflicts` + `POST /conflicts/{id}/resolve`，MCP `conflicts_list` / `conflict_resolve`；闸门决策语义不变（仍走待审队列人工裁决）。决策见 [ADR-0010](docs/adr/0010-conflict-resolution-layer.md)
- **MCP 工具扩容（第一批）**: 8 → 12 工具，新增 `raw_add` / `rollback` / `conflicts_list` / `conflict_resolve`
- Initial release adapted from [aiduMEM](https://github.com/monkey2jack/aiduMIT)
- Storage layer: SQLite + FTS5 + ChromaDB
- Four-path hybrid retrieval: vector + BM25 + FTS5 trigram + decay
- Relevance gate, Tidal coalescing, Fastpath, Dedup, Ebbinghaus forgetting, Chronos
- Shell Hook + MCP dual-mode integration
- Security baseline: loopback binding, SSRF guard, atomic backup, endpoint whitelist




