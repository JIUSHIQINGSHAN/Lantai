# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **离线性能基线工具 `scripts/perf_bench.py`（v0.68 并行流 D 票 `.scratch/v068-perf/01`）**：进程内直调核心函数（FTS/BM25、hybrid_search、build_context、摄取、apply_proposal、apply_forgetting），自建临时 SQLite（真实建表 + 迁移链 + FTS5 trigram）按规模灌入种子化合成语料（默认 1000/5000 两档），输出 P50/P95 基线（JSON + Markdown 双格式）；LLM/embedding 用确定性替身并单列计量、不与本地管线混账，向量库用生产同款内嵌 ChromaDB 0.6.3（装不上自动降级纯 Python 替身并声明）。`--profile` 附 cProfile 热点表并落原始 `.pstats`（可用 pstats 交互复盘）。内置健全性自检（三通道召回/闸门放行/fastpath 命中），不满足拒绝出报告。基线结论：本地管线 5000 行全链 60-70ms，头号热点是向量通道 ACL `$or` 过滤令 chroma 元数据全表扫（17× 劣化，票 `.scratch/v068-perf/03`）。冒烟锁：`tests/test_perf_bench_smoke.py`（不 mock 直调真实实现）。

### Fixed

- **wheel/容器形态三文档写面默认目录逃逸 LANTAI_HOME 收口——settings.data_home 单一真源 + offload 降级可观测（票 85，巡检 2026-11-08 立案轮 10 真 wheel 探针实锚）**：digest_worker/offload_service/wiki_service 三个文档写面的默认输出目录用 `Path(__file__).resolve().parent.parent.parent`（**安装前缀**）推导，无视 settings 已有的 LANTAI_HOME 数据目录推导——发布 Docker 形态（Dockerfile builder `python -m build --wheel` → runtime `pip install` 进 site-packages + `useradd appuser` 非 root，`VOLUME /data` + `ENV LANTAI_HOME=/data` 只保住 db/chroma）下安装前缀 root 只读：默认开启的每日 digest job（`DIGEST_ENABLED=True`）`write_digest_report → mkdir` 必炸 PermissionError，`GET /digest/today` 薄路由零兜底每次现算 **500**；offload 落盘失败经 shell_hook `except Exception` **静默降级**为截断注入零日志（长记忆全文丢失无痕）；wiki mem:sync 降级带 warning 但功能死。修复（票面二选一拍板：归一 LANTAI_HOME 推导，非「容器必须显式设 DIGEST_OUTPUT_DIR」契约派）：`lantai/core/settings.py` 新增 **`data_home` property 单一真源**（LANTAI_HOME 优先、REMEMBRANCE_HOME 兼容位与票 06 动态探测位同被收编、未设回 `_REPO_ROOT`——editable/仓库形态行为零变化，与 `model_post_init` 对 db/chroma 的既有推导同源零分叉，局部 `home` 消重复改挂它）；三写面 `_DEFAULT_*_DIR` 常量全退役、`_digest_dir()/offload_dir()/wiki_dir()` 改挂 `settings.data_home/docs/*`；显式 `*_OUTPUT_DIR` 优先级语义逐字保留（绿锁臂钉死）；`shell_hook._format_offload_entry` 降级臂补 `logger.warning`（fail-soft 语义保持，缺的是留痕；文案避票 50 敏感变量表）；`docs/daily-digest.md` 两安装形态口径 + settings 三处过时注释同步。回归锁：`tests/test_ticket85_wheel_write_dirs_home_anchor.py` 9 臂零 mock（tmp 当 LANTAI_HOME 真调三 dir 函数与 data_home、三写冒烟真文件系统落盘断言、wiki 臂真内存 SQLite 全链；offload 降级臂替身的只是 write_offload_file 的 FS 副作用（AGENTS.md 豁免口径），降级分支与日志行为全真；修复前 **8F/1P 真红**，红/绿日志 `.scratch/v0.68-next-window-2026-11/watch/logs/ticket85-{red,green,family}.log`）+ **真 wheel 探针回跑**（`python -m build --wheel` 真构建解包当 site-packages + `LANTAI_HOME=<临时目录>` 实跑：三目录全落数据根、`write_digest_report` 产物 **INSIDE_LANTAI_HOME=True / INSIDE_INSTALL_PREFIX=False**；探针两处随票更新——退役常量的旧 import 名对齐 dir 函数新契约、裸 `str.startswith` 分隔符伪影（env `C:/...` vs Path `C:\...` 假阴）改 `Path.is_relative_to` 同形对比，伪影系探针自身口径缺陷非实现缺陷）+ 哨兵族随票更新（票 53 datetime AST 哨兵 digest_worker 白名单行号平移 326/604/610→332/610/616，旧 324 注释位退役；精确登记制注因已更新）+ 自审 code-review 双轴收口（Spec 轴无 REAL-BUG——REMEMBRANCE_HOME 回填与动态探测位逐行推演与 db/chroma 同源一致；Standards 轴必修 0——ADR-0016 死符号勘误已收、三写面同构三元暂缓抽 helper 第四写面再议，b2319c42）+ 家族回归 90P 零漂移 + 终树全量 **3784P/0F**（`.scratch/v0.68-next-window-2026-11/watch/logs/ticket85-full.log`，对账票 87 终树 3775 + 本票 9 臂 = 3784 分毫不差）。消费方全景复核：dir 函数全部调用点逐一核验、全仓 `parent.parent.parent` 仅 settings + 三写面 4 处零第四写面。
- **生产环境公网暴露 FastAPI 三件套收敛——/docs、/redoc、/openapi.json 默认关闭 + LANTAI_DOCS_ENABLED 环境开关 + create_app() 工厂化 + context 樊篱缺口同票收口（票 87，2026-11-09 杭州安全组黑盒审计）**：`lantai/api/app.py` 以 FastAPI 默认构造参数起服，公网未带任何凭据 `GET /docs`、`/redoc`、`/openapi.json` 全部 200——186 条路由完整契约（famen/gouchen/qijuzhu/diben 等内部代号面全部随契约公开）与版本号可被任意公网读者零成本测绘、精确匹配版本 CVE（鉴权墙本身完好：93 GET 逐一探测除活探针外全部 401，故 P2 卫生项）。修复：**create_app() 应用工厂**（FastAPI 标准形态）——`docs_kw()` 按 env `LANTAI_DOCS_ENABLED∈{1,true,yes}` 返回三件套 kwargs（默认 docs_url/redoc_url/openapi_url=None），模块尾 `app = create_app()` 生产单例；dev/本地调试显式设 env 打开。**工厂化的来由（reload 退役案）**：初版模块级 env 快照 + 测试 importlib.reload 切换两配置，全量复跑实锚真连坐——129 个测试文件 collection 期绑定旧 app 对象，reload 换掉模块 app 身份后 dependency_overrides 按**对象同一性**查表落空 → ACL 滤空（test_ticket72 arm1b 全量红单飞绿）；工厂形态下生产 app 只在 import 期构造一次（永不 reload），测试按 env 直调工厂造一次性实例，零模块状态搅动。**`FastAPI(title=..., version=...)` 同行保门禁**：release_check「FastAPI /docs 版本」门禁正则（release_check.py:39）对本文件源码扫描，拆行即版本一致性假红——ruff format 按 E501 显示宽度口径（CJK 算 2 列）两轮拆行均实抓（lint 门禁测试 + test_collect_refs 假红），终以工厂体内局部名压短（docs_kw/app）保单行 91 显示列，正则实匹配复验。不影响面逐一锁死：`/health`、`/api/memory/health`（Kuma 哨兵）纯静态 handler 两配置恒 200、`/ui/*` 零 openapi/docs 引用（routes_ui.py 核实）、X-API-Key 管理路由鉴权链零触碰。同票樊篱缺口 ×2（票 64 同根，派单授权，现场复测行号已漂移）：`cognition/context.py` causal_provenance timeline 的 **timestamp 位**（:119，来源描述可含库内正文引用）与 catalog 切面的 **tags 标签位**（:232，用户可控自由文本 AddMemoryReq.tags 直通落库）裸拼进 LLM 可见面——闭合标记逃逸即落在樊篱数据位之外（青鸟 subject/sender 票 63 同根）；自审补口同渲染块 **domain/lane 位**（domain 声明为枚举位但存在 LLM curator 传导写入点 promoter.py:540 proposed_patch 直落，间接可控）。**同族契约随票更新**：test_muci_api_smoke seam4 旧断言锁「user/fact: 3 items」裸渲染字面量（正是病灶形态），对齐樊篱新契约按位断言（票 63 qingniao seam4 先例同构）。回归锁：`tests/test_docs_surface_hardening.py` 6 臂零 mock（真 app 实例不发网络；三件套是构造期参数，同进程两配置靠工厂造一次性实例；裸 TestClient 不走 lifespan 为仓库先例 test_async_ingest.py:61——带 lifespan 拉 apscheduler digest_catchup partial pickle 炸点实抓后改裸形态；fixture env 清理走 monkeypatch 自带 teardown——首版裸 os.environ.pop 被 G2 哨兵实抓改登记式写入器；修复前 **3F/3P 真红**，红/绿日志 `.scratch/v0.68-next-window-2026-11/watch/logs/ticket87-{red,green}.log`）+ 家族回归 134P 零漂移（context 四件 + auth + release_check 门禁 + gewu 因果双件 + 裁决/青鸟樊篱 + 失败归属）+ 终树全量 **3775P/0F**（`.scratch/v0.68-next-window-2026-11/watch/logs/ticket87-full3.log`，与 full2 的 3773P+2F 对账 3773+2=3775 分毫不差；full2 两败即 reload 连坐与 seam4 旧契约，均 95824368 收口）。自审 code-review 双轴三发现全收（lifespan 冗余 import os 删除、domain/lane 补口、测试 env 赋值风格统一）。工单行 17 附带发现拍板：`/` 与 `/ui/*` SPA 壳**保留**（数据面全部 401 API、壳体零 docs 依赖、零数据泄露）。相邻面登记：认知切面元数据位樊篱巡检（promoter domain 直落列等）开票 88 needs-triage。
- **/memory/add/raw 去重归属收窄在 REST 入口未接线——principal 不透传，票 20 的跨属主去重守卫在 REST 是死代码（票 79，票 20 三 caller 两接一漏的 REST 残面）**：`add_raw_memory` 的去重归属收窄以 `principal` 形参为开关（`principal=None → 不过滤`，worker/MCP 内部路径不能空转），三个 caller 中 MCP（`mcp.py` handle_raw_add）与 obsidian（`sync_obsidian_note`）都透传了，唯独 REST `add_raw_memory_route`（`lantai/api/routes_memory.py`）只传 `user_id/tenant_id/agent_id` 不传 `principal=ctx`——REST 直存面全部走无归属过滤臂，持 key 者提交一段与任意他人 verbatim 行 sha256 相同的文本（读同一篇公开文档即可构造）即命中对方行、拿到对方 `memory_id`（`{"memory_id": B 的 id, "dedup": True}`），调用方（obsidian 同步形态）拿此 id `s.get` 即把 A 的双链实体从 B 的记忆行连出去（票 20 危害链在 REST 原样成立）。与票 24 子项 2 的部分唯一索引无交互冲突（去重命中发生在 INSERT 之前、不产生新行；接线后 A 看不见 B 的行、插自己的行，属主不同不撞 `(memory_type, key, user_id)` 索引）。修复：一行透传 `principal=ctx` + 注释引票 20/79，与 MCP/obsidian 同口径；**service 层零改动**——`add_raw_memory` 签名与 principal=None（worker/MCP/obsidian 内部路径）语义逐字零漂移。回归锁：`tests/test_ticket79_raw_route_principal_wiring.py` 5 臂零 mock（param_env 真内存 SQLite，去重 SELECT/路由函数/落库全真执行，兼为 `add_raw_memory_route` 不 mock 冒烟测试；红臂 ×2 决定性——直调 + TestClient `dependency_overrides[get_current_user]` 端到端（test_write_side_ownership.py:264 先例），修复前 **2F/3P 真红**（两臂实测均返回 B 的 id + dedup=True）；护栏臂 ×3 防修废——同主去重不废、NULL 属主老行仍判「重复」不判「新建」（docstring 价值观拍板保持）、A 提交后 B 的行原样未动；红/绿/家族日志 `.scratch/watch/logs/ticket79-{red,family,full}.log`）+ raw 家族十二文件 222P 零漂移（test_raw_memory / verbatim_obsidian / verbatim_key_normalization_smoke / verbatim_search_ownership / write_side_ownership / obsidian_sync_ownership / mcp / mcp_principal_wiring / injection_guard / source_url_protocol_smoke / tongpao_federation_storage_smoke / dedup_ownership——零既有断言冲突）+ 终树全量 **3767P/0F**（`.scratch/watch/logs/ticket79-full.log`；首跑 1F 为 forgetting gate 工作区 porcelain 绊线误伤全量跑与自审编辑并发、工作树定住后复跑零再现，run1 日志 ticket79-full-run1-concurrent-edit.log 在案）。
- **MemoryItem 状态/版本快照 RMW 残留三面收敛——探颐消解丢用户纠正（P1）、revive/unconsolidate 版本重号、裁决终态被覆写（票 78，票 23 收口后全仓巡检的票外同族面）**：票 23 验收要求「代码审查确认计数器链路无残留未加闸 RMW」的扫尾票，病灶全为「读快照 → 判定 → 整字段回写」同型（pysqlite 惰性 BEGIN 下纯 SELECT 不进写事务，读与写之间零隔离）：①**P1 探颐消解丢用户纠正 ×2 位点**——`tanyi.py`（execute_tanyi_resolution）与 `probing_service.py`（resolve_probe_response 肯定分支，派单只列前者、后者为现场复核新增的同型遗漏面且是 REST/MCP 生产入口）都是 conf.status 门后 `item.content/version/decay_score` 整组 ORM 写——与并发 correct_memory 交错时按扫描期快照写回，**用户显式纠正刚写的正文被探颐旧语义正文覆盖**（票 23 S8 同级）、version 重号；②**P2 record_ops revive/unconsolidate 版本重号**——`_revive_one`（consolidated→active）与主记忆 retracted 路 `version=(version or 1)+1` 裸写，与并发纠错交错两逻辑迁移共写同一号，checkpoint version_at 链断；③**P3 裁决终态覆写**——resolve 肯定分支 `conf.status="resolved"` 裸翻转，人工裁决已落终态（dismissed）时被探颐覆写（票 22 S6 conflict_service 蓝图同型）。修复：三层机械修全走 `cas.py` 现成 helper 零新机制——①②版本前置 CAS（`atomic_update WHERE version=扫描值 AND status=扫描/active`，写阶段第一条 DML 对最新已提交状态求值）+ 胜方 `refresh` 对齐 ORM 再落 checkpoint；败方如实让路——探颐冲突**保留 open 交人工重探**（`{"status":"conflict","action":"concurrent_modification"}`）、碎片跳过 warnings 如实记（revive 幂等语义天然兼容）、主记忆败方回滚不重复写 retracted；③conf 翻转改 `cas_update WHERE status='open'` 条件认领，败方回滚整笔（含刚写正文），人工终态权威保留。**自审双轴补笔**（Spec 轴两 REAL-BUG 实抓，6ed39eea）：簇形态 `revived += 1` 无条件计数虚报（`_revive_one` 改三态返回 None=败方/tuple=胜方，调用点消费返回值；碎片形态败方 ok:False 不冒充幂等）；unconsolidate 主记忆败方路向量副作用先行（`sync_vector_delete` rollback 撤不回——认领挪到 FTS/向量副作用之前，败方零副作用如实返回 vector_removed=None）。**死代码登记**（#3 病灶项）：lifecycle.py（KnowledgeLifecycleManager）生产零调用方 HEAD 复核属实（grep 全仓仅定义本体 + tests），按票 23 late_correction 残留先例登记不修，登记锁入测试（源码扫出现消费方即红，提醒接线前必须先加闸——test_ticket62 把 supersede 当「真实写列路径」消费的风险单列）。**#4 P3 状态覆写族其余登记面维持 open 待开收口票**（probing 否定分支 conf 翻转 :522 与分支 B archive :680、consolidation_service :450/:576、promoter deprecate/merge/consolidation 分支 :385/:466/:595——status 写 + version += 1 同窗；sanxing 已由票 67 条件化非残留）。回归锁：`tests/test_ticket78_status_rmw_residual.py` 12 臂零 mock（param_env 真内存库 + 跨 session 交错注入，票 23 脚手架同款；utcnow 单缝「先调真再注交错」；修复前 **3F/6P 真红**——探颐 resolved + 纠正被覆盖实锤、肯定分支同型、人工 dismissed 终态被覆写成 resolved 实锤；自审红 2F/9P——簇虚报 + 向量副作用先行实锤，红/绿日志 `.scratch/watch/logs/ticket78-{red,green,family,family2}.log`）+ 家族回归 152P 零漂移（tanyi 三套件 + 票 23 counter_rmw 全族 + adjudication + consolidation + mcp principal wiring + record_lifecycle revive 面；首跑 18F 三处实现笔误当场修正复跑全绿——红臂先行把实现错误拦在家族层）+ 终树全量 **3761P/0F**（自审补笔后终跑 `.scratch/watch/logs/ticket78-full2.log`；当前树再 +1 scripts 扫描臂共 3762 例，Standards 建议收口 ad6338a1）。
- **`pre_compress` 死接线模块删除案——「压缩前抢救」从未接线，两处文档名实不符（票 77，triage 拍板删除）**：`lantai/integrations/pre_compress.py`（`flush_before_compress`）模块 docstring 自述「Hermes 上下文压缩前把最近对话快照异步写入记忆库」、`db.py:23-24` busy_timeout 注释亦以「pre_compress daemon 线程并发写库」为由设 30s——**两处文档都假设它活着**，而全仓 grep `flush_before_compress` 仅命中本模块与测试（shell_hook 只 import host_adapters/host_protocol，MCP/REST/调度器四装配面零触发），生产不可达＝死接线。**拍板删除案**（triage 收口）：接线需宿主协议新增动作帧（跨仓协调成本，P3 不撑）；模块自引入零生产调用无用户依赖；死代码 + 误导文档是纯负债。修复：①删模块（61 行）+ 死接线测试（198 行）；②**活契约保全**（工单未列，执行方普查发现）——旧测试里 `TestMetadataBounds` 五臂（AddMemoryReq.metadata 有界校验，security review 防线）与 metadata→RawDocument.meta 落库臂是活契约覆盖，随迁 `tests/test_ticket77_precompress_dead_wiring_removal.py` 不陪葬；③db.py busy_timeout 注释理由改写（worker/调度器/请求线程多写面），**参数本体保留**（删理由不删参数，对其余并发写路径仍有价值）；④schemas.py metadata 字段注释示例中性化（唯一拿 source=pre_compress 当示例的消费面注释）。回归锁：`tests/test_ticket77_precompress_dead_wiring_removal.py` 9 例零 mock（arm1 find_spec+文件存在+三目录 rglob 残留扫、arm2 注释清理+参数防误删锁、arm3 直调真 pydantic 校验五防线、arm4 消费面注释臂、arm5 真内存库落库臂；修复前 **3F/6P 真红**，红/绿日志 `.scratch/watch/logs/ticket77-{red,green}.log`）+ AddMemoryReq 十个活构造/消费面家族 201P 零漂移 + 终树全量 **3750P/0F**（复跑定账；首跑 1F 为 plugin_channel_timeout 时序脆弱位首暴露、与删除面零关联且复跑零再现，`.scratch/watch/logs/ticket77-full*.log`）。
- **悬镜头部 stats 前后端漂移收口 + `_INDEX_HTML` 死副本删除（票 72，票 64「死渲染路径」家族前端消费面反向同族）**：悬镜（Xuanjing）星图控制台头部 `xuanjing.js:89-94` 消费 `stats.total_nodes`/`total_edges`/`avg_decay_score` 三键，服务端 `xuanjing_service.py` 两分支（空图/有图）只输出 `node_count`/`edge_count`/`lane_counts`/`relation_counts` 从不输出三键——`?? currentData.nodes.length` 空值合并把「本次加载量」冒充全库总量、「平均时效」控件因 `!= null` 恒假永远显示 '—'（写死在界面上的死功能）；`routes_ui.py:515-619` 内嵌 `_INDEX_HTML` 死副本全文件唯一出现处即定义行、无任何路由返回，与活页面（FileResponse 服务的 `ui/index.html`，已长出九视图）漂移成两个「事实源」，未来改错副本即静默回退。修复：①服务端两分支 stats 统一增补三键（`total_*` 别名 + `avg_decay_score` 节点 decay_score 均值 round 3 位——decay_score 语义 1=最新→0=殆尽，×100 即「平均保留率」与 tooltip 艾宾浩斯语义一致；空图 None 非 0，前端 `!= null` 守卫正确落 '—'），两分支键组一致；②删 `_INDEX_HTML` 死副本 105 行单一事实源。回归锁：`tests/test_ticket72_ui_drift_stats_and_dead_index.py` 5 臂零 mock（真内存 SQLite 直调真路由；arm1a/arm1b stats 契约臂期望值取构造字面量独立真值、arm1c 两分支键组一致锁、arm2 死代码模块级锁+源码文本双保险（先例 `test_dead_code_removal.py`）、**arm4 契约纽带自证臂**从 xuanjing.js 源文本提取全部 `stats.*` 消费点断言 ⊇ 测试常量——前端改键而常量未跟时先红，test_monitor_ui.py 读 JS 源交叉验证强形态同构；修复前 **3F/1P 真红**，红/绿日志 `.scratch/watch/logs/ticket72-{red,green}.log`）+ 家族回归 81P 零漂移 + 终树全量 **3757P/0F**（`.scratch/watch/logs/ticket72-full.log`）。自审 code-review 双轴：Spec 轴无 REAL-BUG（avg 方向语义/空图 None 守卫/MCP 纯增量 json.dumps 直传五项 CONFIRMED-OK）；Standards 轴 1 必修已修（`__import__` 取 app 全仓零先例改常规 import）+ 2 建议全收（先例引用改指实存测试、arm4 自证臂补齐，ce8758d9）。跟进登记：index.html:241 tooltip「全图平均」与「本次加载子集均值」措辞漂移属既有文案，建议后续票改写。
- **核心记忆块正文撤出 URL 查询串——PUT /core-memory 契约改 JSON body（票 71，票 50 同族新位点）**：identity/task/policy 三块核心记忆正文（用户会放身份约束、政策口径、乃至密钥口令的地方）经 `PUT /core-memory?block=...&content=...` 查询串传输——前端 studio.js:279-280 `saveCoreBlock` URLSearchParams 拼串、服务端 routes_memory.py:54-57 路由签名设计为查询参数接收，**契约本身**把高敏正文放进 URL；URL 与 body 的本质差别是 uvicorn 访问日志默认含 query string，任何记 URL 的遥测/代理/浏览器历史同样带走（票 50 的 loggable 单行化+截断管日志插值，管不到 URL 本身——必须改契约）。修复：`PutCoreMemoryReq`（block/content/namespace，default 语义保持）改 body 接收、query 只留零参数；studio.js 改发 JSON body（api() helper 已支持零扩展）；服务层 memory_service.put_core_memory 签名与 400/注入拒绝语义零改动。**兼容窗拍板**（普查臂实证）：直接切换不留兼容层——MCP 层无 put 工具（core_memory_get 只读面），PUT 活消费方仅 UI 一处；旧 query 形态转拒收锁（422 + GET 读回验证零穿透）。普查臂正则四分支防漏抓（PUT 同行/键值同行/模板串/TestClient params= 分行形态）。**取证范式立新**（tests/ 无 uvicorn.access 捕获先例，TestClient ASGI transport 不触发 access logger）：真 uvicorn.Server 后台线程（lifespan="off" 绕开 apscheduler job 序列化炸点）+ 真套接字临时端口 + urllib 真请求 + caplog 捕 `uvicorn.access`——全链路真实机制取证非 mock。回归锁：`tests/test_ticket71_core_memory_body_contract.py` 4 臂零 mock（修复前 **3F/1P 真红**，红/绿日志 `.scratch/watch/logs/ticket71-{red,green}.log`）+ 测试面 8 处 query-form 消费点随票更新（e2e 三处 + scratchpad ownership 四处 + console contracts 一处）+ 家族回归 52P 零漂移 + 终树全量 **3753P/0F**（`env -u PYTHONPATH` 清宿主跑，`.scratch/watch/logs/ticket71-full.log`）。
- **会话/事务卫生家族三面收敛——三引擎裸 Session 泄漏池耗尽 + 三省 auto_apply 整体提交调用方会话 + 采纳流程 TOCTOU 撤回被物化（票 67）**：①裸 Session 泄漏三处：juanzong.py:62（八调用点）/canshang.py:117（两点）/qijuzhu.py:59（两点）的 `_get_session` 裸 `return db.get_session()`——自建 Session 无 close 无 with，方法返回后会话连同活动事务一起弃置（Session↔SessionTransaction 强引用环），SQLAlchemy QueuePool 连接滞留；dossier_service 八构造点从不注入 session（MCP 卷宗/参商高频调用下每次调用新建引擎→新建 Session），池（默认 5+10）耗尽故障形态为整库 `TimeoutError` 卡死（红测实测小池第 2 次迭代即炸）。修复：三引擎公开方法全部**双轨化**——注入轨道直传 self.session（生命周期归调用方，旧语义保持）+ 自建轨道 `with db.get_session() as s:` 返回即关（对齐 diben.py:134 仓内样板），方法体挪 `_create_dossier` 等单轨私有实现；dossier_service 八函数补 `session: Session | None = None` 注入参数透传构造点（worker 长连接复用语义）。②三省 auto_apply 整体提交：sanxing.py:413-414 对传入 session 直接 `s.commit()`——调用方同一会话上的未完成中间写入（batch 处理中间态）被整体提交、错误部分无法回滚（红测实锚：自愈内部真失败（SQLite RAISE(ABORT) 触发器）后调用方 PendingRollbackError 连坐、外层探针行丢失）。修复：自愈写面收敛 `begin_nested` 保存点——失败仅回滚本操作（调用方外层事务/中间态原样），**注入会话提交权归调用方**（引擎不再代提交），自建会话轨道 with 块内 commit 终态落库。③采纳流程 TOCTOU：sanxing.py:331-400 三个逐项 ORM 写块（`s.get` 读 → 属性赋值 → `s.add`）在 `_check_access` 后无锁读上进行、提交前不复验——扫描时点快照与落库之间记忆被 retract/易主的候选照常物化（红测实锚：第二会话真撤回后 auto_apply 覆写 `retracted→archived`，宁 miss 不脏写破约；属主中途易主则越权写他人行）。修复：逐项 ORM 写块退役，**条件 UPDATE 重验下推**——`UPDATE ... WHERE id=? AND status='active' AND lifecycle 白名单（按动作分流：supersede_retract 诊断对象恰是 lifecycle='superseded' 残留行只验 status，防 retract 物化即 TOCTOU 本意；其余动作验 active/weakened）+ 属主/租户 ACL 谓词（`_reverify_access_predicates`，票 57 口径：非 admin 属主匹配或 NULL、tenant 收窄不排 NULL）`，谓词不成立 rowcount=0 静默跳过该候选不报错。**同族契约随票更新**（票 62/63 seam 先例同构）：test_sanxing_engine_smoke seam2/seam3 与 test_sanxing_api_smoke seam1 注入会话路径补调用方 `db_session.commit()`——旧断言锁的正是半边 2 病灶形态（引擎代提交调用方会话），调用方履行提交义务后再验证持久化。回归锁：`tests/test_ticket67_session_transaction_hygiene.py` 9 臂零 mock（真 SQLite 独立 tmp 库直调真引擎；臂 A1a/A1b/A1c 三引擎自建会话返回后活动事务面 + 臂 A2 小池 50 连发 QueuePool 耗尽故障形态集成锁 + 臂 B 保存点遏制三断言（内部真失败→调用方事务存活/探针行保留/半途未提交）+ 臂 C TOCTOU 撤回跳过正常候选照落 + 臂 C2 属主易主 ACL 重验 + 臂 D 回归双锁（自建路径持久化 + dossier_service 注入拒自建会话）；时序编排经 evaluate_health 公共接缝的继承覆写替身（第二会话真 UPDATE 真 commit，票 52 handler 接缝注入同口径，被测链路全真）；修复前 **7F/1P 真红**（7 病灶臂全红实锤：in_transaction True、QueuePool timed out、PendingRollbackError、撤回被覆写 True is False、service 注入 TypeError；d1 红态即绿），红/绿日志 `.scratch/watch/logs/ticket67-{red,green}.log`）+ 家族回归 96P 零漂移 + 扩面关键字族 126P（dossier/juanzong/canshang/qijuzhu/sanxing/muci/evolution）。门禁补笔（6372a391）：票 53 isoformat AST 哨兵白名单行号随双轨拆分漂移（qijuzhu date 对象合法豁免位 253/264→266/277→自审删死壳后再漂 261/272，注因不变），终树全量（自审修复后终跑）见 `.scratch/watch/logs/ticket67-full-final2.log`。**自审 code-review 双轴**：Spec 轴裁决无 REAL-BUG（双轨完整性/二次分发/保存点 pysqlite 实证/谓词分流双报场景双重拦/rowcount 匹配行计数/异常路径一致性六项全 CONFIRMED-OK）；Standards 轴实抓 1 必修——三引擎 `_get_session` 死壳（双轨化后全仓零调用方）留壳注释名实不符，删除退役 + 哨兵白名单行号再对齐（c1a31840）；跟进登记：muci.py:99 同族裸会话另票、sanxing 写面 None 口径与读面分叉（既有持平）、juanzong 注入会话 commit 归属未统一（既有契约）。
- **青鸟信使 ACL/状态机/注入四半边收敛——跨租户信箱互通、状态机可翻转、subject 绕獬豸借道跨会话注入（票 63）**：青鸟（进程内信使，ADR-0075）四点半边——①tenant 过滤半边跳过：fetch_inbox/acknowledge_letter 的 `if expected_tenant is not None` 使 principal 无 tenant/无 principal 时整段跳过租户过滤（NULL 半边家族），反向 tenant=A principal 看不见 tenant=None 信函（双向不对称），docstring「跨租户不可见」名实不符；②user_id 写而不读：dispatch 落 principal.user_id（None→"default"）但读面全程不读——同租户跨 user 信箱互通，user_b 可读可签收 user_a 的单播信函；③ack 无状态机：无条件覆盖——acked 再 ack 翻转 acknowledged_at、rejected 可翻回 acknowledged；重复投递每次新 letter_id 不幂等（全文件无 message_key 概念）；④注入面：dispatch 只对 content 过獬豸，subject 只过非空检查直接入库，消费面 context.py 青鸟切面 `[主题: ...]`（与 `[发信方: ...]` 同族面）裸渲染——subject 是用户可控自由文本，信使正是为跨会话投递而生，注入载荷借道 subject 直进 LLM 可见面。修复（对齐票 57 家族范式，进程内 dict 无存量迁移负担）：①写侧 dispatch tenant None→"default" + 读侧 expected_tenant None→"default"，过滤无条件建立；②单播信函非 admin 仅信函属主（letter.user_id==viewer_of）可见/可签收——**fetch 侧属主不匹配跳过隐藏而非抛错**（若他人跨 user 投递的信函混进本人信箱，抛错会让本人读不了自己的信箱，且错误信息侧信道暴露信箱构成，宁 miss 不越权）；**ack 侧单行操作抛 PermissionError**（403 语义）；**广播信函（receiver="*"）双面豁免属主闸**（租户闸兜底，ADR-0075 租户内广播语义保留，绿锁臂钉死）；admin 全表旁路（绿锁臂）。③ack 状态机：终态不可翻转（异向 ValueError 拒绝且状态保持），同向幂等重入返回现态 + already_* 如实标注不覆盖 acknowledged_at（record_ops already_retracted 幂等先例同构）；④dispatch 入口 subject 与 content 双闸过獬豸（整条拒）+ context.py 青鸟切面 subject/sender 渲染包樊篱（wrap_as_data 对闭合标记中性化——獬豸只防指令覆盖形态，樊篱闭合标记形态只能靠渲染面围栏拦，两层防线各一臂）。**设计决策点（票面拍板登记）**：A. delivered 中间态裁掉——全仓零写点零消费（fetch 只读不改状态），两态直达 pending→acknowledged|rejected，给 fetch 加置态副作用破坏「拉取只读」契约且破坏既有 smoke，改面更大；B. message_key 显式参数——同 key 同 namespace 重复投递返回原信 + deduplicated 如实标注单行幂等；非 sender+receiver+subject 确定性派生（隐式派生会把不同业务方同主题投递误合并，脏写）。**Fog 备注实测**（票面要求顺带验证）：进程内邮箱并发投递 8 线程×6 封全数落箱无异常（CPython GIL 下 dict 单操作原子）；**进程边界结论**：_QINGNIAO_MAILBOX 是模块级进程内 dict，多 worker 各持独立实例互不可见、重启即蒸发——跨 worker/持久化须先答进程边界问题再动状态机（ADR-0075 单进程模型内自洽）。回归锁：`tests/test_qingniao_acl_state_machine.py` 15 臂零 mock（真引擎直调进程内信箱，_QINGNIAO_MAILBOX fixture 逐测清空防串染；注入臂载荷经 check_prompt_injection 探针验证确定性命中/确定性绕过——防守卫升级后假红；修复前 **9F/6P 真红**，9 病灶臂全红、6 绿锁臂红态即绿，红/绿日志 `.scratch/watch/logs/ticket63-{red,green}.log`）。**同族契约随票更新**：test_qingniao_api_smoke seam4 旧断言锁 subject/sender 裸渲染字面量（正是本票病灶形态），对齐樊篱包裹新契约。**自审 code-review 双轴**：Spec 轴实抓真 bug——message_key 幂等索引只键 `(namespace, message_key)`，跨租户/同租户跨 user 同 key 投递返回他人原信 dict（含正文）绕过两道闸（越权泄漏），修索引键升 `(namespace, tenant_id, user_id, message_key)` + 跨租户/跨 user 两红臂锁定（a3f376c5）；Standards 轴顺手感同收（dispatch user_id 手写收敛改 acl.viewer_of 单一真源、already_ 动态键复用 new_status、测试 fixture 补清幂等索引）。遗留登记：MCP qingniao_messenger fetch_inbox 出口透传裸 letter dict（结构化 JSON 契约面，与 context 侧缺口同族不同面）归票 64 并案。家族回归 76P 零漂移（qingniao 三套件 + context 四件 + fence/裁决樊篱 + REST/MCP 语义）。
- **认知读面 lifecycle/status 过滤缺失家族八面收敛——已归档/已撤回/已被顶替的记忆持续进入目录、摘要、图扩散与仲裁（票 62）**：MemoryItem.status（管道态）与 lifecycle_status（认识论态）正交，supersede 只写 lifecycle_status=SUPERSEDED + superseded_by **列**（写点：lifecycle.py:73-75 / late_correction.py:91 / zhiming.py:322，全仓零写点写 structure dict 键），status 仍是 "active"——只过滤 status 不过 lifecycle 的读面让「已取代/已撤回/已归档」行持续混入。八面修复（统一白名单范式 `status=="active" 且 lifecycle_status ∈ {active, weakened}`——weakened「仍参与检索但权重降低」放行、retired「等同于 archived」排除；Evidence 表无 lifecycle 列，读面 = `status=="active"` 对齐 zuozheng.py:148 同仓正确范式）：①muci.build_catalog 全文查询零过滤 + 全库/板块活跃计数只看 status（superseded 计入活跃）；②famen._collect_all_skills 有 status 缺 lifecycle + get_skill_detail 直取门同病；③zuozheng inspect_evidence target / src_mem 邻居扩散 / verify_claim 三处缺 lifecycle；④conflicts._evidence_stats 的 Evidence 查询无 status 过滤（docstring 自述「active」名实不符——archived 证据拉低 evidence_strength 参与仲裁评分）；⑤canshang arbitrate / multihop target_node 的 `s.get` 主键直读**双绕一切 SQL scope**（retracted 侧凭 confidence 0.99 + 更晚事件轴胜出仲裁），补逐行 status+lifecycle 校验，拒绝留日志（宁 miss 不脏写）；audit 三条路径（memory_ids 直读 + 两 SQL 面）补白名单；⑥gouchen dredge/reconstruct 候选集漏 retracted 笔削内容（ADR-0047：主张错误即全检索面禁用，不可自动复活；archived 沉埋行照常打捞对照臂锁定）；⑦sanxing 死诊断修复——`structure.get("superseded_by")` 读 dict 键而全仓零写点写该键 → 「已替代仍活跃」诊断恒假（死代码），改读列 `item.superseded_by`；扫描集刻意保持 `status==active` 不加 lifecycle 白名单（superseded_but_active 残留正是三省要检出的缺陷对象，白名单会把它滤成二阶死代码——退出由 generate_remedies(auto_apply) 落地）；⑧evolution.propose_beliefs/propose_rules 的 Evidence 评分查询无 status 过滤（archived 证据把 evidence_quality 拖垮、pattern 晋升候选被吞）。全仓 grep sweep（工单第 2 条）：其余 7 处 `.get("superseded_by")` 读点逐一核验均合法（mcp.py:600 MCP 参数、supersede.py:93/97 + hybrid.py:1014 model_dump 列回显（列键在 dump 输出中）、sanxing 输出 dict 构造、hybrid.py:303-312 + reflector.py:224-226 本地边映射、promoter._LIFECYCLE_FIELDS、edges.py:156 + graph_retriever.py:283 输出构造）——sanxing.py:160 是唯一死键读点。回归锁：`tests/test_ticket62_readface_lifecycle_filters.py` 13 臂零 mock（真 SQLite 内存库 StaticPool 直调八引擎，superseded 行一律走真实写列路径 KnowledgeLifecycleManager.supersede 禁手搓字段；每臂带 active 对照行锁语义回归零误伤；仲裁臂 caplog 锁拒绝留痕；红态反证 13F@078a6021（临时 worktree 挂红测提交亲证，红/绿日志 `.scratch/watch/logs/ticket62-{red,green}.log`））。**同族契约随票更新**：test_sanxing_engine_smoke seam1/seam3 旧种子喂死 dict 键（锁的是死诊断行为）改真实写列路径，21P。自审 code-review 双轴：Spec 轴实抓臂 3c 对照行白种（播种后未断言主路径），补锁 `mem-claim-ok ∈ supporting_nodes`（1739639d）；Standards 轴登记读面谓词在仓内已分叉两套口径（本票 `{active,weakened}` 白名单 vs shiyi.py `!= SUPERSEDED` 排除式——retired 放行），单一谓词真源收敛登记为后续票。工单引用漂移如实登记：`list_digests` 实为 build_catalog、`lantai/evolution/` 实为 lantai/cognition/evolution.py（派单现场校准已覆盖）。全量门禁 3723P/0F（`env -u PYTHONPATH` 清宿主跑）+ lint 门 ruff format/check 全过。
- **FailureRecord 归属列缺失收口——三读面全库裸拉、他人教训物化为本人 Belief（票 61，迁移 v43）**：`FailureRecord` 表没有 tenant_id/user_id 归属列——失败记录从出生就无法归属，三个下游读面想过滤都无从过滤：①润物摘要 `runwu.py` failures 轨 `select(FailureRecord)` 全库裸拉，A 的失败教训进 B 的认知摘要（rules 轨票 57 已收敛，failures 轨本票同收）；②反省 `reflection.py` 全库失败蒸馏 + `_stamp_owner` 把**从全库失败教训蒸馏出的 Belief 盖上调用者属主**——A 的教训以 B 的名义落库、被 B 的检索召回（物化即冒名）；③心斋切面 `context.py` 失败切面无身份过滤、无排序、`top_k` 负值反向切片把最旧失败喂进 prompt；④MCP `handle_search` 的认知摘要注入不传身份，rules/failures 双轨在无身份摘要面跨用户泄漏。修复：根修加列（迁移 v43，nullable 保存量兼容，旧行 NULL 不回填——存量回填口径属 ready-for-human 决策点，票面回执列明「ownerless 保系统态（推荐）vs 全归 default」两选项）；写面唯一产生点 `record_feedback` 出生即回填归属（principal 已过 ensure_can_delete 校验，反馈人是天然属主；principal=None worker 留 NULL 系统态）；三读面按 viewer_of 收敛（None→"default"、SYSTEM_VIEWER/admin 全表、OR IS NULL 旧行人人可读——票 57 家族口径）：runwu failures 轨（tenant 只收窄不排 NULL，ownerless 旧行不因租户条件丢失，既有 runwu smoke 回归臂实证）、反省蒸馏来源限定本人 + ownerless 系统态（`failures_scoped` 随行为真实化：具名 True / None+admin False，票 cognitive-write-gaps/01 诚实标注语义保留）、切面身份过滤 + created_at 降序取最新 k 条 + top_k 入口先验显式拒绝（票 66 拒绝语义，自审从失败切面处上移 build() 入口）。防回潮：`tests/test_failure_record_ownership.py` 12 臂零 mock（真 SQLite 内存库直调三引擎 + 真迁移链 v42→v43 老库建表——**红测防假绿三前置实录**：种子先 commit 落行再判列补归属（rollback 吞行→库空假绿）、反省臂种两条同签名失败（单条聚不出 pattern 断言空过）、StaticPool 下 session 连接 commit 后关闭归属 UPDATE 走独立连接；修复前 **10F/1P 真红**——1P 为 legacy_null 现状锁（红态全库裸拉下旧行可见=合法现状），红/绿日志 `.scratch/watch/logs/ticket61-{red,green,green-review}.log`）+ 家族回归 621P 零漂移 + 自审 code-review 两轴收口（top_k 入口先验上移、context.py 弃 `_viewer_of` 私有副本改 acl.viewer_of 单一真源、NULL created_at 沉底、tenant 收窄补臂、名实相反测试改名）。A/B 摘要隔离与反省隔离的红态实锚：B 的 Belief 内容含 A 教训原文（`教训：A 的私有部署流程必须先冻结快照`）以 B 属主落库，修复后全库扫零命中。
- **直书精度洞收口——反问/兼语/来源句不再被 1.0 置信度写成自称事实（票 56）**：`ZhishuEngine` 负向检查只看句尾后缀（`?/？/吗/呢/么`），反问句换任何句尾即漏放——`我是不是该辞职了` 被写成「用户自称：不是该辞职了」confidence=1.0 落 fact lane（fastpath 候选顶格加权进 gate，污染身份画像且考功/沉潜全链跟着走）；`(.+)` 贪吃全尾把兼语句（`我叫他明天来开会`→自称「他明天来开会」）与来源句（`我是从上海来的`→自称「从上海来的」）截成自称；`我是不是搞错了什么` 的 None 纯靠句尾「么」撞后缀表侥幸，句尾换字即翻车。修复（规则引擎对模块自述契约「宁 miss 不脏写…疑问句坚决放行给大模型」的确定性违反，非 LLM 抖动）：①反问形状负向检查 `_RHETORICAL_SHAPES`——自称/职业/居住地九前缀后紧跟疑问形态（是不是/不是/还是/算不算/难道/究竟/怎么/为什么/为何/啥/什么）即整句放行给大模型，形状判定取代后缀侥幸；②兼语/来源形状——捕获值以代词（他/她/它/你/我）开头或 `^从.+(?:来|而来)` 来源结构开头即放行（同族自然覆盖旧码实锤同为脏写的 `我叫我们组开会`/`我的名字是他告诉我的`）；③**自审 code-review 实抓 release 改道真 bug**——`break` 只跳内层 pattern 循环，`我是一名他尊敬的老师` 职业轨 release 后落进下一条更不具体的自称轨仍 1.0 脏写「一名他尊敬的老师」（rail 还换了），修 `return None` 终结整个 fact 轨（release 后无后继轨能合法接住）+ 改道形状红锁两例；④来源正则提为模块级预编译常量、代词表去复数冗余（单字 startswith 语义已覆盖）。下游契约零变化：`_build_result` 十一字段形状锁（fastpath/extractor/rail/lane/summary/claims/methods/constraints/actions/topic/extractor_confidence），`dialogue.py`/`memory_service.py`/`evolve_worker.py` 三消费面不感知。config 轨第二模式「把 X 设为 30 吧」语气词收尾捕获值「30 吧」自查完成登记为债（值形态治理另票，正形状回归臂锁定零漂移）。回归锁：`tests/test_zhishu_engine_smoke.py::TestZhishuRhetoricalPrecision` 五臂真函数直调（三脏形状修复前 dict 修复后 None、句号收尾反问 None 来自形状判定、改道红锁、正当自称/职业/居住地/备忘/配置逐字锁、契约十一字段锁；修复前 2F 真红，红/绿日志 `.scratch/watch/logs/ticket56-{red,green,green-review}.log`）+ 家族回归 503P 零漂移 + 全量 **3697P/0F**（PYTHONPATH= 清宿主跑——本轮实锤宿主 PYTHONPATH 指向 hermes 环境 site-packages 其 pydantic_core 损坏，探针子进程继承后 import 落错环境，此前门禁「存量红」实为该污染所致非代码债）。
- **datetime 序列化出口双形态收敛——`iso_utc` 单一真源 + AST 哨兵防回潮（票 53）**：同一概念字段（created_at/updated_at/generated_at 等）在不同出口序列化形态不同——形态 A（`utcnow()` aware → `+00:00` 后缀）vs 形态 B（DB 列读回 → 无后缀），部分站点叠 `timespec="seconds"` 截掉微秒成第三形态；JS 消费端 `new Date(...)` 无后缀按本地时区解释、带 `+00:00` 按 UTC 解释，悬镜面板两类字段同屏差 8 小时（Asia/Shanghai）。修复：`lantai/core/time.py::iso_utc(dt)` 与 `parse_iso_utc` 对称成单一真源——naive 按「naive=UTC」约定 `replace(tzinfo=UTC)` 补后缀（**绝不可走 astimezone**：naive 调 astimezone 按本地时区解释平移 +8h 墙钟全变）、aware 一律 `astimezone(UTC)` 折算、`timespec="microseconds"` 全精度（Python isoformat 在 microsecond==0 时省略小数段，不显式给 timespec 分裂第三形态）、None → `""`；44+ 出口站点收敛（async_ingest/candidate/consolidation/evolution/kaogong/muci/qijuzhu/shishi/qingniao/provenance/scheduler/xuanjing/memory_service/temporal/dossier/scene/conflict/checkpoint/recall_report/routes_verification/monitor/overview/graph/wiki/eval staged/dialogue/metrics/retention/telemetry/wanbi_retriever/provenance_service/record_ops/work_item/probing + cognition to_dict 面 cehou/diben/niyi/gouchen/sanxing/shiyi/zhaji/zhiming/late_correction）。防回潮：`tests/test_datetime_serialization_forms.py` AST 哨兵扫全 lantai 裸 `.isoformat(`（白名单按 (file, line) 精确登记制：date 对象无时区概念 2 + eval 语料 hash 稳定性 1 + digest_worker date MCP 序列化 4 + SQL cutoff 操作数 1，逐条注因）；行为锁 candidate_service（形态A）与 memory_service（形态B）同形态同精度、naive 不漂移臂、零微秒臂、None 臂，禁 mock 真链路直调。**自审 code-review 两轴实抓 2 真 bug**：①`wanbi_retriever.search_atomic_facts_by_query` 的 `as_of` 形参 `Any`（调用面传 ISO 串），批量改写后 str 直入 `iso_utc` 炸 `ensure_aware`，挂载链零阻断吞成空事实——5 例基线绿/HEAD 红**真回归**（jigu_as_of/cascade_supersede/genglou×2 族），修 `iso_utc(_ensure_utc(as_of))` str/datetime 双收；②`telemetry.prune` cutoff 是 SQL 比较操作数非 JSON 出口——`operation_logs.created_at` 由 sqlite3 驱动以空格分隔落盘，改 iso_utc（T 分隔）后同日历日行因 `' ' < 'T'` 提前 ~23h 被剪，修回空格分隔 + `datetime()` 归一两侧解耦分隔符形态；③muci `or None` guard 恢复；3 处测试旧契约断言（genglou_correction/genglou_ingest/jigu）对齐全精度 `.000000` 形态（时刻数值零变化）。门禁基线对账：release-check 4 例 + install_host_hooks/legacy_schema_parity 3 例基线 b8032b13 同红（环境性存量非本票引入，如实申报）。哨兵遗留登记：票文「裸 isoformat() 之外的 datetime 直传 JSON」面（票 52 checkpoint 类病灶）不在 AST isoformat 射程，属票 52 收口范围；悬镜 UI 消费端出口统一 `+00:00` 后 `new Date` 无歧义零改动。
- **日志卫生面八处收口——用户可控值无单行化/截断直落日志（票 50）**：磨勘 query 原话整条进日志（REST `%0A` 换行伪造独立日志行 + 检索内容第二份无生命周期留存）、沉潜成功日志携带记忆正文 `content[:30]`（正文属最高敏资产，日志却是无保留策略的第二份留存）、RSS URL 原串（`?token=` 凭证参数）、探颐 query 20 字窗（内部换行存活）、札记 session_id、`validate_api_url` 异常带完整 URL（key-in-URL 密钥整条入日志）、SDK/pydantic 异常文本透传（4xx 回显 provider 响应体、pydantic v2 `[input_value=…]` 即 LLM 原始输出）、宿主可控复合串错误路径（勘合 key / coalesce key / request_id）——八族同修：`lantai/core/logger.py` 新增 `loggable(text, limit, keep_tail)` 单一真源（换行/C0/C1 控制符转义字面量压单行 + 码点安全截断 + `keep_tail` 真尾部语义供 URL 排障 + O(limit) 有界预切，超长输入不全串转义）；磨勘 query 改记 16 位指纹（`core.text.query_norm_hash` 单真源上移，与 `retrieval_log._norm_hash` 同归一口径；不取票面备选 query[:50]——截窗仍可携伪造行前半，指纹一次关死两个病灶，裁量已登记票面）；沉潜成功日志删正文插值只记 master.id/簇大小/len；`validate_api_url` 异常 message 只带 scheme/host；同族自审补口：advisor `ParamValidationError` 臂（内嵌 LLM 可控字段）、consolidation `chat_json` 失败臂、`sanitize_source_url` 弃 `stripped[:200]` 只记 scheme/host、retrieval_log `event_id` 全函数五处（REST 回执体可控）。哨兵防回潮：`tests/test_logging_hygiene.py` AST 扫全仓 logger 调用，参数表达式直用 query/content/url/key/event_id 等原文变量即违例（`loggable()` 包装与 `len()` 长度标量豁免、白名单空集+精确登记制、接收者覆盖 `getLogger` 内联形态），八处位点 + 自审补口全部锁住。回归锁：四臂真链路不 mock（磨勘真 app 真内存库 `%0A` 伪造行 caplog 实锤、沉潜真折叠链 off 直写路径正文片段实锤、URL 臂真校验真 reject 透传密钥实锤；修复前 **4F 真红**，红/绿日志 `.scratch/watch/logs/ticket50-{red,green,green-review}.log`）+ 家族回归 220P 零漂移 + code-review 自审 10 发现全收口（keep_tail 语义反转与有界性为自审实抓真病）。
- **数值边界与散点卫生九位点收口——recency 上钳 + 裸 confidence 拒收 + falsy-or 吞零 ×2 + or 0.8 抬权 + 假矛盾两档判定 + dedup 竞态 schema 兜底 + 终态收样本 + 截断硬编码（票 68）**：①`conflicts._temporal_recency` 未来 event_time 产生负 days_old → recency 巨值碾压仲裁权重（单条 3000 年时间异常行即可带偏整场冲突裁决）；修：`max(days_old, 0)` 上钳 rec∈[0,1]，未来时间不奖励，正常过去语义零变化（现状锁臂）。②`niyi.deliberate_proposal` confidence 裸 float——`1.5/-0.2` 越界静默吃进风险评级、`"high"` 报「could not convert string to float」调用方无从自查且 REST 面炸 500；修：先验 `float()` 可转 + [0,1] 界校验，ValueError 带 confidence 指引（拒绝而非静默钳制，宁 miss 不脏写）。③`lifecycle` falsy-or 吞 0.0 ×2——`confidence or 1.0`/`decay_score or 1.0` 让零值记忆**永不触发** weaken/retire（零置信记忆逃过降级扫描）；修：显式 `is not None` 判定。④`zuozheng` 证据节点 `src_mem.confidence or 0.8`——零置信源记忆被静默抬成 0.8 权重参与证据链；修：`min(edge.confidence, src if src is not None else 0.8)`，None 缺省语义保留、0.0 如实传导。⑤`canshang` 假矛盾：正文互斥判定用单字字符集 `set(content)` 交叉 ≥3——对自然语言几乎恒真（的/是/在级高频字），互斥词对各自出现一次即判「直接矛盾」，无关主题成片误报（红证：「系统管理员启用定时备份功能模块」vs「下班后管理员关闭照明电源设备」判直接矛盾、「该功能支持自定义快捷键设置项」vs「鼠标硬件不支持自定义宏按键编程」同判）；修：**两档判定**——新增 `_STATE_ANTONYM_PAIRS` 名词性状态反义子集（单机/集群、单节点/多节点：两词即状态本体，同现即同一对象相斥状态断言，真矛盾可零实词交叉——multihop 因果审计锁依赖此档，拆除该档会让「多主集群高可用」vs「仅有单机独立运行」漏检）；其余动词性开关/否定衍生对改 jieba 词级实词交叉 ≥3 或同实体（jieba 既有声明依赖零新增），真矛盾现状锁臂（启用 vs 关闭同一对象）仍检出。⑥`evolution.detect_patterns` dedup_key check-then-act 竞态无 schema 兜底——双线程同签名可双行落库（确定性红证：dedup_key 仅有普通 index 无唯一约束）；修三层：`tables.CognitivePattern.dedup_key` 升 `unique=True`（create_all 新库即唯一）+ `migrations.py` **v42**（存量库先按 dedup_key 去重保最早 rowid——模式行是派生数据同签名语义等价，再 DROP v29 落的非 unique 旧索引**重建同名 UNIQUE**——索引名与模型声明对齐才过 v40 索引同步对账；dedup_key NULL 行 SQLite NULL 互不冲突不追溯）+ 撞键方 `IntegrityError` 回退更新在先行 last_seen（宁 miss 不脏写：不产生第二行也不静默丢本轮归纳信号）；`TARGET_SCHEMA_VERSION` 41→42 单一真源同步。⑦`cehou` 终态窗口收样本无界——graduated/rolled_back 后样本照收、计数照涨、报警面无条数上限（大库单窗口报警风暴），且终态指标已不参与任何状态跃迁；修：`_record` 终态 ValueError（先验后写）。⑧`zhaji` 落库截断硬编码 `[:DEFAULT_MAX_CHARS]` 无视 `max_chars` 参数——digest 面与落库面上界分裂（调用方传 200 实落 1000）；修：两写点同用 `max_chars`。**票面漂移如实登记**：dedup_key 病灶实际在 `evolution.py`（票面指向表格行号漂移），按实修；位点 5（shishi tenant 缺席/ownerless 可改写/admin .first() 无序）票面自标 Fog 行为红不可确定性断言、位点 9b（diben 无 session_id 全表加载）经 Python 侧 ACL 过滤后行为正确（票 57 口径）仅效率面——两者不入红测，Answer 登记裁量。回归锁：`tests/test_numeric_bounds_hygiene.py` 18 臂零 mock（真引擎真内存库直调；修复前 12F/6P 真红——并发臂非确定性如实记录，确定性红证由 schema 臂承担）。**测试装置两课**（首跑实测教训）：并发臂禁用 StaticPool 共享单连接——双 Session begin/commit 交错互踩事务报「cannot commit - no transaction is active」，测的是装置不是产品并发路径，改临时文件库各线程独立连接；`threading.Barrier(2)` 在 worker 先炸时永久挂死整跑，改异常捕获 + `join(timeout=60)` + 判活断言。家族回归 453P 零漂移（迁移族 36 例红为 TARGET_SCHEMA_VERSION 未同步所致，同步后全绿）。红/绿日志 `.scratch/watch/logs/ticket68-{red,green}.log`。
- **摄取面 HTML 不剥 + 截断卫生二项收口——RSS 标签零进库 + GRAM 截断上观测 + incoming_ref 全长/带标截断（票 49）**：①`rss.py` 摘要原样入库：feedparser 内建 sanitize 只清 script/style/iframe 危险元素，`<p>/<a>/<table>` 普通标签随正文经 RawDocument → 候选/FTS → `evidence.py` 截 200 字宿主注入帧全链带标签噪音；修：`_strip_html`（BS4 `get_text(separator=" ")` 一步剥标签 + 实体解码，块级标签间补空格防粘连；**声明依赖 bs4/lxml pyproject 已有零新增**——票面「全仓无 BeautifulSoup」只 grep 了代码面未查依赖清单，如实修正）；纯文本摘要（arxiv）零改写对照臂锁定。②`_GRAM_TERMS_MAX=24` 静默截断查询尾部 gram（AND/OR 两路共用、注释自认假阳性面、截断事实零观测）；修：`_bm25_terms` 拆出全量 + 纯谓词 `bm25_terms_truncated`（截断事实是**查询的确定函数**）+ `RetrievalEvent.fts_terms_truncated` 新列（迁移 v41，is_system_noise 同款 BOOLEAN DEFAULT 0 先例；v40 模型索引同步在旧库先于 v41 跳过本列索引 → v41 显式补建；无表库跳过防炸链——首版守卫缺位被迁移族 9 例钉面拦获）+ `log_retrieval` 落列（原查询抹除前照算，同 is_system_noise/estimated_tokens 既有范式）；工单红测字面形态（search_fts 自写事件行）被否如实申报：search_fts 只持裸 conn、无回执生命周期上下文，自写 pending 行会被回执扫描器改判 missed 并与真实事件行双计污染弱标注统计。③incoming_ref（冲突账本「对方主张」引用位）无标记硬截 200：内容来源四位点（late_correction supersede/expire、decision 两处）改存**全长**（冲突判别语义完整性优先，工单口径；content 有摄取上界），调用方 reason（无界外部输入）保留 200 钳制但走仓内既有 `truncate_codepoints` 带省略号；dialogue/extractor 候选摘要 `[:400]` 同形对齐带标（工单点名的 dialogue.py:245 行号漂移至 :274 且其同形真身在 extractor 离线兜底——两处同修）。回归锁：`tests/test_ingestion_truncation_hygiene.py` 七臂（真 feedparser 本地 feed 字节——fetch_with_safety 网络边替身票 69 豁免口径；真迟到更正/闸门流程真内存库——decide() 经向量降级 rowid 召回真流驱动，chat_json 注入 RuntimeError 拦 LLM 相位为外部边界替身；修复前 **7F 真红亲证**：`<p>` 标签入库实锤、200/245 全长断言实锤、谓词 ImportError；红/绿日志 `.scratch/watch/logs/ticket49-{red,red2,green,family}.log`）+ 迁移族钉面 10 例随 v41 合法更新 + 相邻族 676P 零漂移 + 终树终跑全量。工艺如实申报：修复未提交窗口内 `git checkout HEAD` 误还原九文件工作树——修复重放脚本化后立即提交（红复验先提交后摘除）；dialogue 臂首版误走 fastpath（zhishu 直通摘要无截断面，红相理由不纯）——改非 fastpath 文本走真提取链后红相重验。
- **MCP 面参数钳制缺口家族收口——16 位点裸 int/裸 float/裸 strptime 全边界化 + cehou 引擎先验后写单咽喉点（票 66）**：MCP 工具参数直通家族新位点（票 26/27/42 的 MCP 面）——`limit=-1` 在法门/目次是 Python 切片语义反转（`assets[:-1]` 静默切掉末尾）、在青鸟是 SQLite `LIMIT -1` 无限拉取、佐证 `max_depth` 无上界直放大 BFS 扩散面、测候 `score_delta=1e400`→inf 与 NaN 直落 REAL 累计列**永久毒化**（NaN 比较恒假、inf 累计后一切裁决失真）、`required_days=0` 让按天观察闸形同虚设、str `mock_days_observed` 落库后 `days >= req_days` TypeError 毒化该窗所有后续巡检（票 60 只救了事务，类型脏值仍可入库）、`10**9` 一检即伪造毕业、起居注坏日期裸 strptime 报「time data does not match」调用方无从自查。修：mcp.py 新增 `_int_param` helper（bool 是 int 子类显式排除防 `true→1`；界对齐 REST 既有 Query/Field 双面同钳、REST 为真源；**拒绝而非静默钳制**）收 dossier_focus/muci_catalog/qijuzhu days+limit/famen/zuozheng max_depth+limit/qingniao 六 handler，qijuzhu date 格式错带 `YYYY-MM-DD` 指引拒绝；cehou.py 引擎单咽喉点（同守 MCP 裸传面与 REST 面）三处**先验后写**：create `required_days [1,90]`/`min_samples [1,1000]`、record_sample finite + latency 非负（score_delta 允许负=评分下降）、inspect `mock_days [0,36500]`；routes_cehou.py `SampleRequest` 补 `allow_inf_nan=False`（pydantic float 默认放行 JSON Infinity/NaN，json.loads 默认解析这些扩展）+ latency `ge=0`、`InspectRequest.mock_days [0,36500]`；routes_qijuzhu.py 路径日期坏格式 422 带指引（票 41 同病收口）。票 07 广告哨兵 `_consumed_keys` 收编 `_int_param(params, 字面量)` 间接消费形态（键字面量仍在调用点，AST 逐 handler 精确归因，非豁免表，保持 fail-closed）。回归锁：`tests/test_mcp_param_clamp_gaps.py` 20 臂零 mock（真 handler 链直调真内存库：str mock_days 被拒后同窗正常巡检不炸的毒化臂、`1e400`/NaN/负耗时、`required_days=0/-3`、`max_depth=10**6`、famen 两条技能只剩一条的负切片红证、REST Infinity 422 等；4 现状锁如实申报——3 合法参数臂 + REST create `ge=1` 已拦；修复前 **16F/4P 真红**，红/绿日志 `.scratch/watch/logs/ticket66-{red,green}.log`）+ 触达模块家族 454P 回归零漂移 + 哨兵族 8P。剩余缺口如实登记：MCP 面其余 handler 的裸 `int()`（cognitive_summary 四参、shiyi/shiyi_pipeline/jiaochou limit 等）归票 26「未声明参数整体治理」未收。
- **REST 参数边界裸奔面收口——28 位点 limit/top_k/min_conf 全边界化 + 冲突可见性下推 SQL（票 42）**：17 条路由 + 1 个 service 的数值参数零校验直入——负值 `limit` 在 SQLite 是 `LIMIT -1`＝无上限（全表捞回）、在 Python 切片侧是 `rows[:-5]`＝「去掉末尾 5 条」（结果集静默错形），`top_k=0` 让检索空转，`min_conf=1.5` 让置信度过滤永假。修复面：写副作用面（考功/知命审计）1000、候选类（candidates/crystals/sources）200、checkpoint 100（完整行快照放大尤甚）、只读列表 500、`top_k` 100 对齐 `SearchReq` 既有上界、`char_budget` 100000、`max_chars` 10000、float 阈值 `[0,1]`；越界即 FastAPI/pydantic 422（票 41 语义：可校验输入在边界拦）。顺手收口 `conflict_service.list_conflict_events` 性能债：原 `.all()` 全表捞回后内存过滤可见性再 `rows[:limit]` 切片，现可见性 `in_()` 子查询与 `limit` 全下推 SQL（空集 `in_([])` 编译恒假，bob 零可见语义不变）。AST 签名哨兵（先例票 09/26 fail-closed 白名单 22 条——服务层双校验与跨票归属位点逐条注因）进测试防回潮。回归锁：`tests/test_rest_param_bounds.py` 12 臂零 mock（TestClient + 真内存 SQLite StaticPool；9 红臂 limit=-1/-5、top_k=0、min_conf=1.5 全 422 + 2 现状锁（conflicts limit=2 → 2 事件、bob/admin 可见性）+ 哨兵臂；修复前 **10F/2P 真红**，红/绿日志 `.scratch/watch/logs/ticket42-{red,green}.log`）+ 触达模块家族 397P 回归零漂移。
- **配置面死开关双项接线——`DIALOGUE_ENABLED` 对话通道关得掉 + 察窗到期轮询上调度（票 43）**：①`DIALOGUE_ENABLED`（`settings.py:83`）全仓零消费点——四个对话摄取入口（REST `/dialogue`、MCP `add_dialogue`、潜移 async submit、冷启动导入）只受 `DIALOGUE_MIN_CHARS` 阈值约束，设 false 对话照常摄取（票 34「配置显示有门槛、实际从不评估」的反向同族：关不掉而非不生效）；拍板**接线**（票 34 先例）：`DialogueDisabledError(ValueError)` + `assert_dialogue_enabled()` 单一真源落 `ingest_dialogue` 漏斗首行（通道开关先于输入校验，四入口统一覆盖、禁止散写），潜移提交口同调 fail-fast（被拒请求不建任务不占勘合键，免得宿主轮询出一条 failed 假任务）；REST 两路由译 **409**（服务端配置拒绝，非调用方输入问题——票 41 语义纪律不得落 422 滥用口径），MCP 分发器与导入逐条计数经异常继承自动获得零改动；导入返回面透出 `ingest_errors`（此前只在内部累计——`imported:0 + errors:0` 无从区分「没得导」与「通道被关」，透出即结构化留痕防「静默不跑」新病灶，口径同票 34 跳过留痕）；guard 落点偏离票面建议（settings.py `is_dialogue_enabled`）：落通道模块 dialogue.py——异常类型+判据+留痕同域内聚，settings.py 保持纯配置容器。②`check_shadow_due`（`parameters/runtime.py:168`，判定链完备）在 `core/scheduler.py` 注册面零引用——影子观察窗打开后**永不到期评估**，promote/rollback 裁决在生产不会发生（ADR-0027/测候 ADR-0073 链断在最后一环）；注册 interval 作业 `shadow_check`（`seconds=SHADOW_CHECK_INTERVAL_SECONDS`，形态同 `refresh_runtime_params` 先例：无总开关、空转廉价——无 observing 窗即空回）。工单可选项 `/admin/shadow-check` 热触发端点拍板跳过（仓内无热触发范式，不扩面）。回归锁：`tests/test_config_dead_switches.py` 七臂（真内存库真摄取链禁 mock 被测链路，摄取文本全取 fastpath 形态全链确定性零 LLM 依赖；调度器注册锁用 BackgroundScheduler 类壳——`test_receipt_miss_scrub` 仓内范式，被测行为是注册配置本身；修复前 **6F/1P 真红亲证**：四入口照常摄取 + 候选真落库实锤、注册面零 shadow_check 实锤；对照臂锁 flag=true 主路径逐字不变；红/绿日志 `.scratch/watch/logs/ticket43-{red,green}.log`）+ 相邻族 90P（dialogue/param_shadow/async/receipt_miss/import/scheduler）回归零漂移 + 终树终跑全量。剩余风险如实登记：开关 false 时导入面逐行 warning（大导入日志噪声，诚实留痕可接受）；察窗作业无 WORKER_LAST_RUN 登记（monitor 面不含此作业，同 refresh_runtime_params 现状）。
- **context 任务相关度恒零收口——jieba 分词接入，两面排序恢复「相关度优先」（票 65）**：`cognition/context.py::_task_relevance` 用空白分词对任务描述与记忆文本取 token 集算 Jaccard——中文无空格，整句切成一个长 token，交集恒空 → 相关度恒 0，`/cognitive/context` 与 `/context/prompt` 两面排序退化纯置信度/时间序，「任务相关」承诺（票 13 广告面家族）实际失效；英语路径标点粘着 token（"deploy!" 与 "deploy" 判不相关）同病。修：`_relevant_tokens` 助手（jieba cut + strip/lower + len>1 过滤单字与标点——校雠 `jiaochou.py:95` 同源口径，jieba 本就是声明依赖零新增）；Jaccard 契约不变，空面/单侧空早退 0.0 不炸。回归锁：`tests/test_context_task_relevance_smoke.py` 五臂零 mock（纯函数直调：中文同题异述 J=1/3>0.3 票面阈值、无关内容 J=0 区分度、空面三形状、"deploy!"/逗号句两英语臂；排序臂真 SQLite 内存库 + 真 `CognitiveContextBuilder.build` 直调——db 构造注入零 monkeypatch，低置信相关记忆对高置信无关记忆的反转锁，修复前纯置信度序实锤；修复前 **3F/2P 真红**，红/绿日志 `.scratch/watch/logs/ticket65-{red,green}.log`）+ context/cognitive 关键字族 157P 回归零漂移。
- **verbatim 身份键原文 sha 分裂双份记忆收口——身份键过归一缝 + 存量双键并查（票 46，ADR-0085 同缝）**：verbatim 是内容寻址（sha256 即 id 语义），但键对**原文字节**取 sha——IME 全半角翻转、macOS NFD、网页粘贴零宽字符产生不同字节 → 不同 sha → 同一逻辑内容两条 active verbatim 并存、双双进检索挤占预算，更正链（supersedes）可能只覆盖其一；`add_raw_memory` 不走 `find_similar` 余弦去重（只有提取路径走），obsidian sync 每 sync 一次多存一份的原始病在宽度变体下复发。修：`build_verbatim_item` 身份键改 `sha256(normalize_for_index(content))`（票 45 归一缝同源，全角/NFD/零宽变体同归一键）；`add_raw_memory`/`_store_imported_memory` 判重改**双键并查** `key.in_([归一 sha, 原文 sha])`——存量旧口径行与脏内容字节级重写经原文 sha 命中，不迁移不追溯（宁 miss 不脏写；残余缺口＝脏存量行 × 字节不同改写，收口需键列迁移未立项）；正文仍逐字节原样（归一派生键 ≠ 内容改写）。回归锁：`tests/test_verbatim_key_normalization_smoke.py` 七臂零 mock（真 SQLite 内存库真 FTS5 + `add_raw_memory`/`_store_imported_memory`/`build_verbatim_item` 直调，embed 走 conftest autouse 外部替身；宽度变体直存/导入双侧分裂、NFD 变体、存量 raw-sha 行漏判、新行键纯函数臂 + 正文哨兵、字节级同文两态锁；修复前 **6F/1P 真红**，红测常量修正（全角冒号补空格成纯宽度变体）后旧代码复跑同 6F/1P 机制闭环；红/绿日志 `.scratch/watch/logs/ticket46-{red,green}.log`）+ 直触家族 24P（含 `test_raw_memory` 并发同文锁随新键契约更新查询口径）+ verbatim/raw/dedup/import 关键字族 171P 回归零漂移。
- **归一缝缺失收口——索引/查询/案牍去重/列表匹配/聚类签名/检索指纹六面零归一的确定性零召回（票 45，ADR-0085）**：全仓 `unicodedata` 仅检测闸两处瞬态 NFKC（獬豸「只检测不改写」），SQLite trigram 按精确码位切 gram、Python `\w` 保留全角字母——全角↔半角（「ｄｅｃｉｓｉｏｎ」vs「decision」）、NFC↔NFD 双向**确定性 miss**（非概率性降级）；正文一个 ZWSP（网页粘贴常态）把「机器学习」的 trigram 邻接撕开查询 gram 零命中；RTL override 透传宿主终端可致视觉伪装；案牍去重键 `casefold` 把 Ａ(U+FF21) 折成 ａ(U+FF41) 而非 a——全角标题变体不归组出双份工作项；反思聚类签名/检索指纹只 `.lower()` 不折宽度。修：**单一归一缝** `lantai/core/text.py::normalize_for_index`（NFKC 折宽度 → 剥 Cf 类零宽/bidi 控制符 → NFC，幂等），六个消费点同口径过缝：FTS 索引副本（`sync_fts`/`index_fts`）+ 查询关键字抽取（`_bm25_keywords`，AND/OR 两路共用）+ 案牍去重键与列表匹配（`_normalize_duplicate_text`/`list_work_items`）+ 聚类签名（`evolution._get_signature`）+ 检索指纹（`retrieval_log._norm_hash`）。**边界（ADR-0085 核心）**：只归一派生数据（索引副本/哈希键/匹配串），`MemoryItem.content` 用户资产零触碰（獬豸同一条边界；哨兵测试钉住 ZWSP 仍在原文）。存量：FTS 索引派生数据全量重建 `rebuild_fts(session)` + 运维入口 `scripts/rebuild_fts.py`（幂等，不 commit 事务归调用方）；去重键/签名/指纹存量行不追溯（宁 miss 不脏写）。回归锁：`tests/test_unicode_normalization_smoke.py` 十二臂**零 mock**（真 SQLite 内存库真 FTS5 trigram：全角+ZWSP 正文/全角查询/NFD 正文三向召回臂 + `rebuild_fts` 存量重建臂 + 正文哨兵（两态皆绿）+ `candidate_group_ids` 归组臂 + 列表匹配臂（真 `list_work_items` + 体内 `MonkeyPatch.context` 接线，conftest 模块卫生口径）+ `_norm_hash`/`detect_patterns` 登记项臂；修复前 **10F/2P 真红**；红/绿日志 `.scratch/watch/logs/ticket45-{red,green}.log`）+ 直触家族 195P + hybrid/fts/检索/案牍关键字族 282P 回归零漂移。如实登记：向量通道 embedding 输入不过缝（模型对全角/零宽敏感度远低于 trigram 码位敏感性，Chroma 重建代价高，真出问题另立案）；`coalesce` 幂等指纹与石室完整性摘要的字节级哈希是正确行为零触碰（票面已核查）。
- **出站面四病收口——arxiv feedparser 二次抓取原语断根 + LLM 出站时长预算立法 + 限长流式断连 + max_results 钳制（票 48）**：①`arxiv.py` 把 `r.text`（str）喂 `feedparser.parse`——安装版 feedparser 6.0.14 对 str 且 scheme ∈ (http/https/ftp/file/feed) **自己用 urllib 发起第二次抓取**（无 timeout、无私网校验、跟随重定向、file:// 白名单；官方 docstring 明写勿传不可信 str）——响应体被操纵（明文 http + TOCTOU 窗）时 ingest worker 即替攻击者抓取内网/元数据地址或 file:// 本机文件回读。修：`fetch_with_safety(url)` 统一防线 + 响应体 **bytes** 直喂（str 分支结构性不可达，一行断根）+ `http→https`。②LLM 客户端未传 `timeout/max_retries`——SDK 默认 600s×2 叠加 tenacity 3 连击，单次 `chat_json` 最坏 ≈90 分钟占死 ingest job（逐 doc 串行）与 4 许可并发闸（hook/REST embed 等位 30s 后降级，检索注入持续退化）。修：三设置立法 `LLM_TIMEOUT=120/LLM_MAX_RETRIES=0/LLM_INGEST_TIMEOUT=60`（SDK 层零重试——应用层 tenacity 已有界，双层重试是乘法放大；最坏 90 分钟 → ≈6.5 分钟，单请求墙 120s）；`chat_json` 增预算传导 `timeout` 参（`_chat_once`/`with_options` 范式同 embed D-04：预算模式一次机会即止不重试），ingest 提取链 `extract_candidate` 传导 `LLM_INGEST_TIMEOUT`（既有 except 降级路径承接超时）。③`fetch_with_safety` 5MB 限长是**事后检查**——非流式 `client.get` 先把响应体完整读入内存再判 len，恶意源（无限流/缓慢灌注）超时窗内可灌数十 GB，同进程（REST+scheduler+MCP 单进程模型）OOM 陪葬。修：`client.stream()` 逐块累计、超限即断（重定向跳同样流式，响应体不落内存只取 location）——真服务器真 tracemalloc 实测峰值 **21.1MB → 有界**（< 上限+2MB）。④arxiv `max_results` 取自 source config（config dict 无 schema 约束）任意大整数，每 60 分钟自动放大——钳制 [1,100] + 越界 warning 留痕。回归锁：`tests/test_outbound_budget_smoke.py` 七臂（真 feedparser 本地监听直测危害锁两态皆绿：str 触发二次抓取连接抵达、bytes 零网络；arxiv 生产缝 bytes/https/钳制三断言；get_client 立法臂读实例属性；预算单发缝 `_chat_once` 恰 1 次调用；提取链 deadline 传导；本地慢滴真服务器真 tracemalloc 峰值臂 + 小体逐字对照锁；mock 面 = getaddrinfo/fetch_with_safety/观测 wrapper，AGENTS.md 允许口径；修复前 **5F/2P 真红**；红/绿日志 `.scratch/watch/logs/ticket48-{red,green}.log`）+ ingest/ssrf/adapter 家族 36P + llm/ingest 关键字族 170P 回归零漂移。既有 `test_arxiv_adapter_assembles_fields` 随缝迁移（httpx.get 替身 → fetch_with_safety 替身，字段组装断言零变化）。
- **REST 错误语义面三组收口——模板病 9 处统一 kaogong 版 + 可校验输入 422 收口 + review 200/ok:False 译 409（票 41）**：①**模板病**（4 生成系路由 9 处）：`except Exception → 422` 把 DB 写锁/Chroma 宕机/引擎内部 bug 全说成「你的请求有问题」（4xx 监控口径失真）且 `str(e)` 把内部异常文本泄进响应体（红样实测「database is locked (/tmp/lantai/x.sql)」入响应）——gouchen/mokan/scratchpad/zhiming 统一 kaogong 版模板（`PermissionError`→403、`ValueError`→422、其余放 500 全局 handler；zhiming 两站点前置已有 ValueError 子句分别合并为 not-found 感知 404/422 分流与纯 422 子句）；②**可校验输入裸奔 → 500**：checkpoint cleanup（`max_sessions=0`）改 `Query(ge=1)`、terminal graph（limit 越界）改 `Query(ge=1, le=100)`——边界拦 422；obsidian sync **两层阈值矛盾**修正：`ObsidianSyncReq.content` max_length 50 万→**20 万**（与下游 RawMemoryReq.content 及 service `_VERBATIM_MAX_CHARS` 同值——verbatim 通道直通 20 万闸的下游，obsidian 承诺不得超下游）；checkpoint write 的 session_id<3 已被票 54 `SESSION_ID_PATTERN {3,128}` 边界拦 422（工单病灶 2 第一条现树已收口，现状锁实证）；③**票 01 同病残留**：`POST /candidates/{id}/review` 的 approve 路径 propose 返回 None（LLM 不可用/target 不可寻址）时 service 返回 ok:False、路由原样 200——REST 面译 **409 Conflict**（service dict 契约零变化：MCP handle_candidate_review 与 auto_triage_service 消费方不受影响）。回归锁：`tests/test_rest_error_semantics.py` 九臂（TestClient 真路由 + 真服务链 + 真内存 SQLite，禁 mock 被测链路；故障注入为工单 TDD 明示形态；修复前 **7F/2P 真红亲证**：模板臂 422+内部文本泄响应体、AST 哨兵 9 处、三输入臂 500、单真源锁、review 200+ok:False 实锤——candidates 臂以 chat_json 外部替身（确定性 update+幽灵 target_key）复验红样后转绿；红/绿日志 `.scratch/watch/logs/ticket41-{red,green,family}.log`）+ 家族 114P + 终树终跑全量。残留如实登记：propose 的 None 双因（LLM 不可用/target 不可寻址）在 review 契约中未区分（错误文案一律「target memory not uniquely resolvable」且 candidate_status 一律 gated）——语义拆分属 candidate_service 契约演进，本票只收 REST 状态码。
- **幽灵源边不落地 + 边读端点判活——deprecate 建边口径对齐 + 撤回契约覆盖边读面（票 40 子项 1+2）**：①`apply_proposal` deprecate 分支把 `prop.evidence_ids[0]`（LLM curator 输出）直接落成 supersedes 边的 source、零存在性检查——同文件 consolidation 分支有对照口径「`src is None` → 不建边（边指向幽灵即脏写）」，deprecate 漏同款；幽灵边经 reflector R1 装进 `superseded_by` 映射，目标被 admin 合法 unarchive 复原后即被误标 superseded 候选。修：建边前 `s.get` 存在性校验，幽灵 id **不建边**（`logger.info` 留痕、提案照常留痕、deprecate 主语义零变化）。②`edge_service._edge_visible`/`_filter_visible_batch._ok` 只查端点行存在+属主不查 status——已撤回/已归档记忆的边对非 admin 照常返回，撤回契约「全检索面禁用」在边读面失守（`GET /edges/{id}`、supersedes 链、因果链）。修：两路径补端点 `status == STATUS_ACTIVE` 判活（batch owners 值升级 `(user_id, status)` 元组）；admin/`principal=None` 面不受此滤（与属主语义同构）。**自审补口**：`get_chain`/`get_causal_chain` 的三处内联可见性过滤（只查存在+属主）同补判活——否则非 admin 经 `GET /edges/{id}/chain`、`/causal-chain` 仍拿到软态端点的边（链读红样实锤后转绿）。回归锁：`tests/test_edge_ghost_and_status_faces.py` 四臂（真内存 SQLite + 真服务层全链 apply→unarchive→health_scan / retract→list_edges，禁 mock 被测链路；修复前 **3F/1P 真红亲证**：幽灵边 `source='mem_ghost'` 落地实锤、retracted/archived 端点的边照常返回；对照臂锁 active 面不过滤过头；红/绿日志 `.scratch/watch/logs/ticket40-{red,green,family}.log`）+ 家族 85P 含 consolidation 对照口径回归 ✓ + 终树终跑全量 3561P/0F。工单明示只攻无拍板子项 1+2：子项 3（/terminal/graph 回流需 UI 拍板）、4（zhiming 软指针）、5（relation 白名单）零触碰；存量幽灵边 R1 误标残留如实登记（reflector 读侧不在落点清单，存量对账属运维面）。
- **插件层对 serve 子进程读路径零超时收口——承诺的 5s/30s 硬超时常量首次接线（票 73）**：`hermes-plugin/lantai-hook` 模块头承诺「单次请求 5 秒硬超时（防卡死）」，但 `_REQUEST_TIMEOUT`/`_DIALOGUE_TIMEOUT`（`:92-93`）全文件零引用；`_wait_ready` 与六个 `_call_*` 通道在 `_lock` 内逐字节 `read(1)` 无墙——serve 子进程「活着但不应答」（`_prewarm()` 建向量库连接无超时且在 stdin 循环之前，launcher_mcp 双进程共写同库的 chroma.sqlite3 锁竞争即可触发）时首次 `read(1)` 即永久阻塞且握着 `_lock`，`_ensure_proc` 与其余通道全部跟进冻结；`pre_llm_call` 是 Hermes 同步回调 → 宿主整轮对话冻死，无异常、无日志、无自愈（探针实测：挂死子进程直调 `_call_hook`，7s 后线程仍阻塞）。修复：新增 `_exchange_line` 看门狗（写一行请求 + 读一行响应整体受墙保护）——`threading.Timer` 到期 kill 子进程，阻塞中的 write/read 以 EOF 返回，抛 `TimeoutError`（⊂ OSError）路由进各通道**既有 except 分支**（静默降级 + `_proc` 置 None，下次 `_ensure_proc` 按 `poll()` 存活判据自动重启恢复服务）；注入四通道（hook/checkpoint/backfill/veto）挂 `_REQUEST_TIMEOUT`=5s，会话收尾二写（dialogue/checkpoint_write）挂 `_DIALOGUE_TIMEOUT`=30s（含 LLM 提取，不轻易中断收尾写）；`_wait_ready` 就绪探测挂 `_READY_READ_WALL`=60s——独立于探测 deadline（15s 只挡迭代之间）：冷启动加载 10-15s 是合法等待（探测阻塞本就是等加载），60s 兜底真挂死，慢加载零回归。竞态窗口（响应恰在超时瞬间到齐被误杀）后果是本轮宁 miss、下轮重启，如实登记。冒烟锁：`tests/test_plugin_channel_timeout.py` 八臂零 mock（真「活着但不应答」子进程 `python -c sleep` 模块态注入直调七通道，修复前 **8F 真红**——线程判活超墙全实锤；恢复链用真 responder 子进程经 `LANTAI_SHELL_HOOK` 注入 NDJSON 应答脚本端到端验证「正常应答零漂移 → 挂死超时 → 下次调用重启恢复服务」；对话/探测墙宽测试内收窄 1s，注入主通道按承诺 5s 原值跑；红/绿日志 `.scratch/watch/logs/ticket73-{red,green}.log`）+ 插件家族 73P 回归零漂移。
- **两条硬删路径补双端 MemoryEdge 同事务清理——悬空边写侧源头消灭（票 38）**：`MemoryEdge.source/target_memory_id` 无外键约束（tables.py:311-325），边清理全靠应用层；`DELETE /terminal/memory/{id}` 级联清单（FTS+审计+FactAtomic+向量）与 `promoter.delete_memory` 级联（purge_facts+FTS+向量）**都不碰 MemoryEdge**——用户删任意参与过 supports/supersedes/links 的记忆，其在场每条边立即悬空、永久残留；读侧分层：非 admin 由 edge_service「端点行不在 owners → 整条隐藏」挡住，**admin/`principal=None`（MCP list_edges、CLI、worker）经 get_edges/get_chain 照常拿到幽灵 id**（edges.py 无存在性过滤），悬空行同时是边数统计噪声与图可视化断头线。修复：两条路径的级联 try 内各补 `delete(MemoryEdge).where(or_(source==id, target==id))`——**与既有级联同事务**（失败整体回滚、记忆不删；零「边先独立 commit」反模式复刻，回滚锁臂实证），范式照抄 `eval/forgetting_quality.py:239-255`；`source_service.delete_document` 无需改（doc 端边自有清理，目标记忆经 delete_memory 删除后其演化边顺带清理——红测臂实证）。**soft 态端点（撤回/归档行）的边零触碰**（工单禁止项：审计价值，读侧已判活过滤——soft 对照锁钉死）；读侧过滤零改动（对照锁钉死）。回归锁：`tests/test_edge_cascade_hard_delete.py` 六臂（真内存 SQLite + 真服务层全链 + 真内嵌 Chroma，禁 mock 被测链路；修复前 **3F/3P 真红亲证**：三主臂悬空残留全量实锤、第三方边对照不动；红/绿日志 `.scratch/watch/logs/ticket38-{red,green,family}.log`）+ 家族 60P + 终树终跑全量 3531P/0F（含并行流票 35/47/57/58/59/60/69/70 新测试，对账吻合）。剩余风险如实登记：存量悬空行无迁移（修复只止新债，对账脚本属运维面）；soft 态边判活统计口径归票 40 读侧状态面；`delete_document` 自有 doc 端边的独立 commit（「边与文档先落库」）是既有形态未触碰（与票 20 病灶 3 的同族性属该票面）。
- **UI 出口防护三病收口（票 70）**：三病灶同属「浏览器出口防护缺失」——①d3 走 jsdelivr CDN 无 `integrity`（CDN 投毒/DNS 劫持 → 控制台页同源任意 JS → 读 localStorage API Key，且每次开页出站违背本地第一）；②全站零安全头（无 CSP/X-Frame-Options/nosniff，控制台可被第三方页 iframe 点击劫持，XSS 得手无纵深）；③recall 页填过 Key 就无条件 `localStorage.setItem`（与主控制台 api.js 已有的「记住此设备」开关语义不齐）。修复：d3 去远端化（本地资产 `d3.v7.min.js` v7.9.0 已在仓，删 CDN 行与 document.write 回退直载本地，零出站零投毒面）；app.py 统一安全头中间件（nosniff + X-Frame-Options DENY + CSP——`default-src 'self'`，script/style-src 按实测内联用量带 `'unsafe-inline'`：routes_ui 内联页 11 个 script、index.html 40 处内联样式，仍拦外部脚本注入这一真实威胁；`frame-ancestors 'none'`；不开 `unsafe-eval`）；recall 页补「记住此设备」开关、默认 sessionStorage 关页即清。冒烟锁：`tests/test_ui_outbound_defense_smoke.py` 四臂（远端脚本源码臂真红 jsdelivr 无 integrity、/ui 与 /health 安全头两臂真红三头全缺、d3 本地资产防假绿回归锁现状锁——防「删了 CDN 忘落本地文件」星图/悬镜全瘫）；Key 落盘为纯前端 JS，按票面 TDD 3 以代码评审 + 手工验证步骤验收（DevTools 断言 sessionStorage 归位）。
- **星图来源节点 `window.open` 伪协议洞收口（票 69）**：入库双口 url 零 scheme 校验——`rss.py:33` 条目 link（feed 作者完全可控字段；同文件 SSRF 门只管 feed 源地址）与 `memory_service.py` 两处 RawDocument 写点（`POST /add` 的 `url=req.url`，`source_id=req.url or h[:12]` 同污染）原样入库，经 `graph.py:170` 节点组装透传到 `/ui/map` 来源节点点击处理器 `window.open(n.url)` 无白名单——攻击者 RSS 源投一条 `javascript:` 链接、与池内记忆连边进 `/graph` 响应，用户点击即同源 JS 执行（可读 localStorage API Key、任意调 API）。修复：`lantai/ingestion/safety.py` 新增 `sanitize_source_url` 白名单助手（scheme ∈ `SSRF_ALLOWED_SCHEMES` 即 http/https 且必须有 hostname；不合格式**置空 + warning**——宁 miss 不脏写，拒字段不拒整条，与 `validate_fetch_url`/`validate_media_url` 的 raise 语义刻意区分）；入库双口 + `graph.py` 出口同闸（出口闸拦直写库/历史存量脏行）+ `routes_ui.py` 点击处理器 `/^https?:/i` 前缀纵深（服务端是主闸）；fastpath/提取路径两写点 `source_id` 同步回退 `h[:12]`。冒烟锁：`tests/test_source_url_protocol_smoke.py` 七臂（助手本体 pass-through/伪协议拒收+warning、真 `RSSAdapter.fetch` fixture feed 双条目、fastpath 直写点 javascript 拒收+source_id 回退+https 现状锁、提取路径写点走 `extract_candidate` 真实离线 fallback 零 mock、星图出口真库真 `build_graph` 直调；RSS 臂仅 `fetch_with_safety` 外部网络打桩属豁免口径）。
- **测候影子视窗内存注册表收口（票 60）**：`cehou.py` 的 `_SHADOW_WINDOWS` 进程内模块级 dict 是影子实验的唯一真源——`_SHADOW_WINDOWS[window_id] = data` 无条件覆盖，A 用户可用同 window_id 静默篡改 B 用户（或 worker）正在观察的实验对照组；uvicorn 多 worker 各一份（读取随机命中）、进程重启全部视窗蒸发（按天观察的长期实验全丢）；`cehou_service` docstring 自称「持久化」名实不符。修复：视窗状态落库新表 `cehou_window`（window_id 主键，create_all 自动建表，无迁移链负担），`create_observation_window` 按 window_id upsert 且属主校验——已存在且非本人 → HTTPException(409) 拒绝（宁 miss 不脏写），admin/`SYSTEM_VIEWER` 豁免，同属主重开 = 原行更新重置计数；owner 恒 `viewer_of` 收敛（None→`"default"`，不再落 NULL 属主窗，票 57 口径）；record/inspect/scan 全走真库（JSON 列原地 append 不被 SQLAlchemy 判脏→整体重赋值；SQLite 往返剥 tzinfo→`ensure_aware` 归一，票 32 口径；scan 行级 ACL 下推 SQL），`_SHADOW_WINDOWS` 删除——多 worker 一致性与重启恢复随落库自动解决。MCP handler 分派段包 try/except HTTPException → 结构化错误载荷（chenfeng unarchive 先例；参数读取留 try 外保 schema 虚广告审计消费点可达）；conftest 模块状态残留护栏 cehou 通道退役（无状态可守），两个模块卫生测试（票 05 续22/23）植入样本换 `kaogong._LATEST_KAOGONG_REPORT`；service docstring 名实修正。冒烟锁：`tests/test_cehou_persistence_smoke.py` 五臂（夺权 409 / 重启恢复 / 双实例聚合 / 同属主 upsert / 转正判定回归，真库真引擎不 mock；重启模拟=清注册表+新实例，非 mock 被测链路）。
- **樊篱属性位零转义收口——session_id 可提前闭合 `<memory_data>` 开标记 + 入口字符集立法（票 54，票 51 同轮）**：`lantai/llm/fence.py::wrap_as_data` 对 `id`/`score` 原样 f-string 插值——`neutralize_fence_escapes` 只中性化**正文**的闭合标记，属性位裸奔：item_id 含 `">` 时开标记被整体提前闭合，注入文本落在**数据位之外、正文之前**（樊篱是 P0 票03 立法的单一真源，本体失守即全线失守）。两条活链 item_id 用户可控：`diben.py` `item_id=f"anchor-{session_id}"` 与 `zhaji.py` `item_id=f"zhaji-{item.session_id}"`，而 session_id 入口零字符集校验（MCP checkpoint_write 只验长度、REST scratchpad 直传）；票 51 同病位：`mokan.py` 磨勘审计标记 `query="{query}"` 原样插值（query 用户全可控）。修复双层：**结构主闸**——新增 `fence.escape_attr` 属性位转义单一真源（`&`→`&amp;` 先行防双转义，再 `"`/`<`/`>`），`wrap_as_data` 两属性位 + mokan 标记 query 位（提取为纯函数 `_render_audit_marker`，行为不变 refactor 前置可测缝）统一过它；正文位不经过本函数——`neutralize_fence_escapes` 继续守正文，「不改写用户字」的纪律不破，正常 id 渲染字节级不变（现状锁锁定）。**入口纵深第二层**——session_id 字符集立法 `SESSION_ID_PATTERN = ^[A-Za-z0-9_\-.]{3,128}$`（`lantai/core/acl.py` 单一真源）：MCP `checkpoint_write`/`scratchpad_write` 显式校验（ValueError → -32602），REST `CheckpointWriteReq`/`WriteScratchpadReq`/`DistillScratchpadReq` pydantic `Field(pattern=...)`（非法 422）+ `POST /scratchpad/{session_id}` 路径形态路由内对解析后 sid 再钳——非法形态写入口即拒，不静默截断（宁 miss 不脏写）。冒烟锁：`tests/test_fence_attr_escape_smoke.py` 十二臂零 mock（真 `wrap_as_data`/`SessionAnchor`/`ZhajiEngine.digest_item`/`_render_audit_marker` 直调：evil 注入中性化 + 正常 id 字节级现状锁 + 两条活链端到端 + 五个写入口正反对照；修复前 **7F 真红**；红/绿日志 `.scratch/watch/logs/ticket54-{red,green}.log`）+ fence/mokan/checkpoint/scratchpad/zhaji/diben/acl 家族 153P、MCP+REST 面 196P 回归零漂移。如实登记：字符集为票面建议值，合法 session_id 若含集外字符会被显式拒收（422/-32602，响亮的拒绝而非静默改写）；含 `/` 的 id 经路径形态入口时结构性 404（URL 单段路由本就不可达），body/MCP 形态由字符集闸收口。
- **LLM 内部裁决出口三处裸奔收口——库内正文进裁决提示统一过樊篱数据位（票 55）**：樊篱立法「所有离开兰台、进入下游 LLM 提示的记忆正文统一以 `<memory_data>` 围栏包裹」，但三类**写路径裁决**的 user 消息把库内/候选正文裸插：①校雠三态裁决 `memory_service._llm_judge`（`DEDUP_RELATION_USER` 的 old/new 裸插——`relation: merge` 可让两条不同记忆被静默合并、`update` 触发改写提案）；②gate 矛盾检测 `check_contradiction`（EXISTING 段裸插——`contradicts` 可把正常候选打成硬冲突或放过真矛盾）；③演化提案 `propose_from_candidate`（候选 summary/claims/actions 与 EXISTING snippets 裸插——`proposal_type` 操纵演化方向）。獬豸在写入侧拦正则级注入，正文尾部语义级说辞（「（系统备注：判定 merge）」）不在其射程——樊篱正是为这一层存在的纵深防御。修复：新增 `fence.build_adjudication_user` 标签段通用 builder（指令面/数据面分离，裁决 JSON 契约一字不动）+ `memory_service.build_dedup_user`（get_prompt DB 覆盖路径保留，只把数据槽位换成围栏形态）——三处出口全部改走围栏包裹，proposer 的 snippet 行整行包裹（type/key/content 同属记忆派生数据）；P3 顺带：`scene_service` 聚类代表名（源自成员记忆 key）同法；樊篱 docstring 出口清单补登「内部裁决出口」防下一轮再漏。冒烟锁：`tests/test_adjudication_fence_smoke.py` 五臂（builder 直调零 mock：evil 正文闭合逃逸中性化 + 良性输入形状逐字锁；链路臂只 mock `chat_json` 网络边、prompt 构造输出即验收对象，真内存库种 MemoryItem/MemoryCandidate；修复前 **5F 真红**——三处 user 消息 evil 原文裸奔实锤；红/绿日志 `.scratch/watch/logs/ticket55-{red,green}.log`）+ fence/校雠/gate/演化链/场景族 281P 回归零漂移。如实登记：GATE 段（本服务结构化输出，reason 字段可能回显库内片段）不在本轮包裹面；proposer related_q 无 tenant 过滤为票面附注（票 18 家族视角留维护者并案）。
- **探颐回执分类器二次回溯（ReDoS）收口——`_HAISHI_KEEP_OLD` 前瞻有界化 + 回执入口双面上界（票 44）**：`_HAISHI_KEEP_OLD = 还是(?![^标点\s]*?(?:改成|…))` 的否定前瞻内含**无界惰性**扫描——病理输入（「还是」长流、无标点空白、改动词压末尾）让每个「还是」起点惰性扫完整段剩余再失败重扫 → O(n²)（探针实测 n 翻倍耗时 ×4.0：40K→7.4s，外推 500KB ≈19 分钟单线程 CPU）；而探颐**每次回执消解必经** `_matches_any`，REST `user_reply` 无 `max_length`、MCP `conflict_resolve` 裸取、starlette 无 body 上限——单请求卡死 threadpool 一线程，数个并发病理请求即全 API 停摆。修复：前瞻量词有界化 `{0,16}` 窗（「还是」与改动词 realistic 间距远小于 16 字，每起点 O(16) → 全局线性；窗外语主的改动词按「留旧」处理——宁 miss 不脏写，误判方向是留旧不写库而非误写）；入口双面收口：REST `ProbeResolveReq.user_reply` `Field(max_length=2000)`（非法 422）+ MCP `handle_probe_resolve` 显式钳制（ValueError → -32602），不静默截断。耗时哨兵锁防同族形状回潮：100K 字符病理输入 `_matches_any` 必须 <1s（修复前红跑 **121s → 修复后全套件 2.2s**，同一套件计时即回归哨兵）。冒烟锁：`tests/test_probe_regex_bound_smoke.py` 六臂（真 `_matches_any` 直调零 mock 真计时；票 02 采纳/留旧语义正反对照三臂锁防去二次化改坏判定——`_UNCHANGED_PATTERNS` 肯定侧/`_NEGATIVE_PATTERNS` 否定侧双面；REST/MCP 真入口臂；修复前 **3F 真红**；红/绿日志 `.scratch/watch/logs/ticket44-{red,green}.log`）+ 探颐家族 162P 回归零漂移（含 CJK 语义/词边界既有锁）。
- **格物反事实全简单路径枚举指数爆炸收口——路径帽/扩展预算双界 + BFS 单会话贯穿 + 入口深度/置信度同源钳制（票 39）**：`evaluate_counterfactual` 逆向回溯是 per-path visited 的**全简单路径枚举**（防死循环不防指数——队列按路径分支复制，链式 k 叉菱形路径数 ≈ k^n），`surviving_paths` 无上限、每队列项 `_get_parent_hops` 各开新 DB session（路径数 × 深度次 SQLite 会话开合）；REST/MCP `max_depth` 双端无界（`10**9` 合法）——任何认证用户自建菱形图后单请求即可拖垮 SQLite 后端（图检索其余三面均已有界，格物反事实是唯一裸奔的遍历面；红测实测：7 环 ×3 叉链式菱形 max_depth=10 内枚举 **243 条路径** > 帽 64，每条路径一条终止队列项各自开 session）。修复三件套：①**双界**——`_COUNTERFACTUAL_PATH_CAP=64` 封顶返回规模 + `_COUNTERFACTUAL_NODE_BUDGET=512` 封顶 BFS 扩展数（每扩展 = 一次父跳查询，先到先停；命中任一界置 `paths_truncated`，partial 语义口径同拾遗 `scan_truncated`——结论照常给出但明确标注不完整；截断且零存活路径时叙事**不脏写**「必要根因」确定性，明示「结论可能受截断影响」）；②**单会话贯穿**——`_get_parent_hops` 闭包提升为方法共享调用方 session（红 fixture 修复后实测全程 **session_opens=1**，修复前每队列项一开 ≥244 次）；死代码 `find_critical_causal_path`（生产零调用、smoke 在用）同款补帽 + docstring 留痕（票面二选一取补帽不删码）。③**入口同源钳制**——`CAUSAL_DEPTH_MIN/MAX = 1/20` 单一真源（`causal.py` 立法）：REST `CounterfactualReq`/`ProjectImpactReq` `Field(ge, le)` + `min_confidence` `Field(0..1)`（越界 422），MCP `handle_causal_counterfactual`/`handle_causal_project_impact` 显式 ValueError（→ -32602），引擎侧双界兜底任意入口直调。冒烟锁：`tests/test_causal_bound_smoke.py` 六臂零 mock（真 `link_causality` 建边 + 真 `evaluate_counterfactual` 直调；会话计数经 `causal.db.get_session` 计数委托——测试体内 `MonkeyPatch.context` 确定性还原，避 conftest 模块卫生哨兵绊线；小图 3 环对照臂锁 18 条全枚举语义零漂移；修复前 **5F/1P 真红**；红/绿日志 `.scratch/watch/logs/ticket39-{red,green}.log`）+ 格物/causal/MCP 家族 74P 回归零漂移。如实登记：`max_depth` 界值 1..20 为票面建议，合法深查 >20 的调用会被显式拒收（422/-32602）；`project_causal_impact` 本有全局 visited 有界，本票零触碰。
- **MCP `checkpoint_latest` 有底本数据后 100% 炸 + 单帧异常杀死整个 stdio 服务收口（票 52）**：三层同病——①`_rows_to_checkpoint` 把 ORM 裸 `datetime` 放进返回 dict，MCP 面无 FastAPI `jsonable_encoder` 兜底，`json.dumps` 抛 `TypeError`（REST 面同数据不炸，digest 票「只有 MCP 侧炸」同构的未立案兄弟；触发条件极低：调过一次 checkpoint_write 即必炸，`LANTAI_MCP_PROFILE=full` 档下可达）；②炸点在守卫外：`handle()` 的 try 只包 `handler(args)`，响应组装（dumps）其后裸跑；③`main()` 行循环对 `handle(msg)` 与响应帧 dumps 裸调——一帧的序列化病**杀死整个 stdio 服务进程，全部工具失联**（宿主看到的不是 -32603 而是服务器死亡）。巡检对账补强出更宽触发器：非对象 JSON 帧（`[1]`）在 `msg.get("id")` 即 `AttributeError`——无需任何工具、无需数据、任何档位都中（`echo '[1]' | python -m lantai.cli.mcp` 单行复现；shell_hook 侧 `parse_host_request` 已修过一模一样的病，MCP 侧漏修）。修复：`created_at` 过 `isoformat()`（全仓出口约定，REST 编码结果逐字不变）；响应组装挪进守卫（dumps 抛错回 `-32603 response serialization failed` 并计入 loop guard 失败——恒返不可序列化输出的工具也要能被熔断，且与 -32602 参数错语义分离）；`main()` 循环兜底 try + `_emit_error_frame`（单帧只值一条错误响应，服务必须活下去，mid 先取保住请求关联）；非对象帧回 `-32600`。登记项随票：MCP 出口与 `host_protocol.render_host_response` 统一 `allow_nan=False`（NaN 不可达有审计背书——RRF/余弦/BM25/除法全有守卫，纯把「统计代码未来引入 NaN → 静默产出非法 JSON 帧」转成显式错），shell_hook serve 循环 render 加守卫（开 allow_nan=False 后不得引入新崩溃向量——插件正逐字节等本帧响应，render 失败降级为协议内错误帧）。冒烟锁：`tests/test_mcp_frame_survival_smoke.py` 六臂（进程内 exec 副本全链 + 子进程 full 档真 tmp 库全链 + datetime/NaN 序列化守卫双臂 + 非对象帧存活臂 + REST 对照臂现状即绿；修复前 **5F 真红**——TypeError 杀 main/AttributeError 杀进程/NaN 静默帧逐臂实锤；红/绿日志 `.scratch/watch/logs/ticket52-{red,green}.log`）+ MCP/shell_hook/checkpoint 家族 259P 回归零漂移。
- **校雠 scan_redundancy O(n²) 全表炸弹收口（票 59）**：MCP 面 LLM 可自主调用的 `jiaochou_collate(scan=true)` 原样透传 limit/threshold，引擎全表拉 active 记忆后 Python 侧两两 jieba 相似度（`limit` 只截输出对数凑够即 break，计算量不减）——1 万条 ≈ 5000 万次分词比对，故障形态是 stdio 单线程宿主整进程无响应而非错误码。三路收口：①候选池硬上界 `SCAN_CANDIDATES_MAX=100`（模块常量不做 Settings 旋钮，同 refine_service 批量上限口径），池 = 最近创建优先（created_at+id 双键稳定序），池内预分词一次再做 set-Jaccard、报告对复用 `compare_and_collate` 单一真源，计算量 O(池²) 恒定（修复前 300 行实测 8.57s → 修复后约 0.1s）；②行级 ACL 下推 SQL where（None 收敛 `viewer_of`="default"、admin/`SYSTEM_VIEWER` 全表、`swarm_shared` 公共结晶人人可读、租户不匹配同判，票 57 六引擎口径交叉），Python 侧第二套判据 `_check_access` 成死代码随删（钩沉处置同判）；③`limit` 钳 [1,100]（bool 拒绝）同时钳计算池与输出，`threshold` 合法区间 (0,1] 越界 ValueError——MCP 派发器转 -32602 错误载荷、REST pydantic 422（`le=200` 收紧为 `le=100` 对齐引擎，不再静默钳制），双面同钳不炸宿主。**口径变更如实申报**：`limit` 语义从「先扫到的 N 对」（插入序敏感，票 05 续20 flaky 根因）改为「最近窗口内最相似的 N 对」，早于池窗口的冗余对不可见——宁界不爆；机制锁重写钉新契约（`test_jiaochou_scan_limit_caps_pool_and_output`）。冒烟锁：`tests/test_jiaochou_scan_redundancy_smoke.py` 八臂（规模/钳制/身份/回归，真库真引擎不 mock）。
- **principal=None 身份收敛第四波收口——六引擎 + 底本二写方（票 57）**：MCP 宿主不透传 `user_id` 时 `_principal_from_params` 返回 None，六引擎把 None 当"内部调用要全表"（acl.py 立法原文描述的"同一个 None 两种解释"病的第四波漏网，票 04/05/06 连收三波后本轮认知引擎逐行巡检再翻出）。①钩沉 dredge/snapshot 无身份**不加任何 where**，全库用户/租户的沉埋记忆全文可见——None 收敛 `viewer_of`=`"default"`（只见默认属主+NULL 老行），显式系统身份/admin 全表（同 kaogong 已写对范式）；死代码 `_check_access` 一并删除（无人消费，行级 ACL 真源即两处内联过滤）；②底本 `synthesize_anchor` **二写方**落 `SessionCheckpoint(user_id=None)` NULL 属主行（读侧 OR-NULL 口径下 NULL 行人人可读，五段工作现场对任何用户公开），且无 upsert 同 session 块行累积、无钳制、无后台通知过滤——owner 收敛 + 对齐受审计写方 `checkpoint_service` 四保护（upsert delete+insert 同事务 / CHECKPOINT_MIN 过短不落 / MAX 截断 / 后台通知整块丢弃，全部新块不合格旧快照保留，票 14 口径），读面 `_check_access` None→True 同收敛；③卷宗 list/get/focus 无身份全放（`_check_access` None→True + 属主比较裸 `self.principal.user_id`）——收敛后 focus 不再外泄他人绑定记忆原文，create owner 落 `"default"` 不落 NULL；④尘封 unarchive 走 `ensure_can_delete` 而其对 None principal 三段 getattr 全落空**不抛 403 直接放行**——MCP 无身份可解封任意他人记忆回 active（record_ops_service 自身无 ACL），闸前收敛：None 只能动 default/NULL、显式系统身份全表（刻意）、具名照旧单一真源；⑤目次/法门/佐证 `_check_access` None→True——收敛（swarm_shared 公共结晶口径不变，前置判断）；⑥润物 rules 面 `user_id=None` **跨用户全表拉规则**——缺省收敛 `"default"`，SYSTEM_VIEWER 哨兵显式全表。本批六引擎无任何 worker/scheduler 调用方（grep 实证），全表场景仅 SYSTEM_VIEWER 臂锁存在性；NULL 老行 `OR IS NULL` 口径全保留。回归锁 `tests/test_identity_convergence_wave4.py` 二十三臂（红 **20F/3P** 逐臂核过病形：无身份全库/NULL 属主/无 upsert/无钳制/原文外泄/静默解封/跨用户规则/MCP 无身份双臂；现状锁臂：具名过滤、跨属主 403、SYSTEM_VIEWER/admin 全表立法语义）+ 六引擎 API/engine smoke 119P + MCP 身份族 133P。
- **披沙批量精炼管线四病收口 + FastAPI 422 NaN 炸 500 系统级防线（票 58）**：①批量 `limit` 无下界——负值在 SQLite 等于无 LIMIT（全表选中进逐条 LLM 改写），service `ValueError` 双界 1..100（上界对齐 param_advice `le=100` 口径）+ REST 双路由 pydantic `Field/Query(ge=1, le=100)`；②改写前无原件备份——summary/claims 原地覆盖后不可回滚，现落 `provenance.pisha_original`（唯一可回滚位，改写三态 refined/rejected/poisoned 统一走 `_apply_refine_result` 单一真源）；③LLM 产物未过獬豸——refined_text 直落 `cand.summary` 进库内所有渲染面（context 目录/to_prompt/樊篱包裹面），现 `assert_no_prompt_injection` 毒闸，带毒不覆盖原文、`pisha_poisoned` 留痕保持 pending_review 交用户裁决（宁 miss 不脏写 + 不静默丢，AGENTS v0.5 纪律）；④已精炼无幂等闸——重复精炼重烧 LLM 且迭代漂移，现单条早退 + 批量 SQL 排除（用裸 `func.json_extract` 而非 SQLAlchemy JSON 路径的 `.is_(None)`：后者编译成 `JSON_QUOTE(JSON_EXTRACT(...))`，而 SQLite `JSON_QUOTE(NULL)` 返回字符串 `'null'` 非 SQL NULL，键缺失行永不匹配、整批扫空——本轮探针实证并回修）。同轮收口：`refine_text` **入口樊篱**——候选正文/上下文/实体映射出 prompt 前一律 `wrap_as_data` 包裹（此前只覆盖出口 `fenced_content`，注入载荷可越出数据位操纵精炼裁决，票 55 同族）；PishaEngine **死代码处置**——引擎版 DB 写面（refine_candidate/_refine_candidate/batch_refine_candidates/_batch_refine/_check_access）与构造器 session/principal 参数整体删除（production 零调用、与 service 版行为分叉的影子实现，「后续接线即爆」），DB 写面单一真源在 refine_service，既有引擎冒烟 seams 2-4 同文件迁 service 层；**票 35 红测发现的 422 渲染疾病升级为全局防线**——app 级 `RequestValidationError` 消毒 handler（FastAPI 422 响应体以 `allow_nan=False` 渲染，宽容解析器吃下非标 JSON 字面量令 error.input 携带 NaN/inf 时 422 自身渲染炸 500，全部 body/query 校验面受益；非有限值替换为 repr 字符串后渲染，红测含 `/cehou/windows` 系统面同验）。回归锁 `tests/test_pisha_refine_guards.py` 十四臂（红 **12F/2P** 逐臂核过病形：DID NOT RAISE ×3、KeyError pisha_original、裸 prompt 无樊篱标记、已精炼 calls==2、total_scanned=3、REST 200/500/200、cehou 系统 500；现状锁臂：limit=2、REST 正常 200）+ 引擎冒烟 4 接缝 + 家族 32P（refine/pisha_api/auto_triage）+ API 面抽查 41P（全局 handler 零漂移）。
- **importance 写入面无界收口——毒行不再炸穿遗忘引擎（票 35）**：`_lane_strength` 对 importance 无下界假设（chat 泳道 base_s=3、boost=5：importance=-0.6 → strength=0.0 → `exp(-days/0)` ZeroDivisionError；-0.61 → exp(+800) OverflowError），而三条写入路径全部不钳制——`apply_forgetting` keyset 分页主循环零容错，**一条毒行让全库衰减与自动归档永久停摆**（探针实测：同页对照行已算好的衰减随异常一起回滚、毒行之后的记忆永不被处理，唯一信号是 /stats forgetting last_run 变陈旧）。四处收口：①REST 更新面（`PUT /terminal/memory/{id}`）importance 拒绝越界与非有限值（422，宁 miss 不脏写）——不走 pydantic `Field(ge/le)`：**FastAPI 422 响应体以 allow_nan=False 渲染，验证错误 input 携带 NaN/inf 时 422 自身渲染炸 500**（本轮红测实测），故 handler 显式拒，与同文件 `assert_no_prompt_injection` 的 422 同惯语；②沉潜生成面 LLM 提取 importance 非有限值跳过该簇（宁 miss 不脏写）、有限越界钳入 [0,1]（`mokan.py:121` 既有习语）；③promoter apply 面 patch importance 非有限值落 REJECTED 终态（拒绝必须终态，防 evolve_worker 对 APPROVED 提案每轮重试＝livelock）、有限越界钳入 [0,1]；④公式地板 `MIN_LANE_STRENGTH_DAYS=1e-6`——历史毒行按地板强度衰减 → decay 立即跌破归档阈值 → 被正常归档，引擎自愈；主循环逐行容错（脏行跳过 + 留痕 + 健康行照常衰减——TEXT importance 经无闸 import 面落库是票 47 收口前的现实入口，探针实证 TEXT 落 REAL 列活着过 ORM fetch、在循环内 TypeError）。存量排查（2026-11-08，真实库 remembrance.db 只读）：4 行、0 行负值、min 0.1 / max 0.5——无存量订正。回归锁 `tests/test_importance_write_clamp.py` 十二臂（红 **11F/1P** 逐臂核过病形：ZeroDivision/Overflow @forgetting.py:145、REST 200≠422 ×3、生成面 -5.0 落提案 + NaN 未跳过、apply 主记忆 -5.0 落库 + NaN NOT NULL IntegrityError、脏行 TypeError 炸穿主循环；现状即绿臂如实申报：PUT 边界 0.0/1.0）+ 家族回归 266P（遗忘族/终端写面/沉潜/提案/回滚/CAS/獬豸二阶闸/调度面）+ 终树终跑全量落文件（`.scratch/watch/logs/full-suite-20261108-ticket35-final.log`）。登记：同请求模型 `confidence` 同型无界（无崩溃向量，未立案随票 66/68 家族收口）。
- **rollback checkpoint 撞号选取收口——全序契约（version+created_at+id）+ 票 23 回归锁（票 37）**：rollback 还原循环虽已不回拨 `version`（票 23），checkpoint 选取仍只有 `order_by(version.desc())` 单键——`MemoryCheckpoint` 对 `(memory_id, version)` 无唯一约束，历史 tie 行（撞号存量）的「上一版」选取**无契约**（SQLite 等值键顺序是扫描序实现细节≠语义承诺，换版本/加索引/换查询形态都可能翻转），探针实测重复回滚方向不确定（redo/空转/跳档随 tie 行序摆动）。修复目标 2（还原不回拨 version + 入口 CAS）**已由票 23 关闭**（工单零.4 亲证，不重做只补回归锁）。本轮落修复目标 1 全序契约：`order_by(version.desc(), created_at.desc(), id.desc())`（id 是 `new_id("ckpt")` ULID 前缀，字典序单调作最终 tie-break）——存量 tie 行即时定序化，无需迁移（工单修复目标 3）。回归锁：`tests/test_promoter_rollback_lifecycle.py` 两臂——①定序臂（红）：手造存量撞号链（两笔 version=2 tie、**插入序与 created_at 序相反**——旧时代戳撞号的现实形态）→ rollback 应按全序取 `ckpts[1]=(2,早期,after=V1)` 还原 V1，现状按扫描序取较晚笔还原当前态**空转**（探针「第二次回滚打回 v2」病灶形态的确定性复现；第一版 tie 臂用 apply 链后插 tie 行跑不红——现状扫描序对等值键恰好稳定（插入序=选取序），改手造链制造确定性分歧，现状绿属侥幸非契约，如实入 commit）；②幂等臂（票 23 回归锁，绿）：正常链连滚两次 checkpoint 链 version 无重复、严格递增 1-4、无「还原到当前态空转 +version」（第二次回滚 content 必变）。红/绿日志 `.scratch/watch/logs/ticket37-{red,family}.log` + 家族 109P（含 test_checkpoint_history_ownership/counter_rmw/evolution_write_ownership 零漂移）+ 终树终跑全量。剩余风险如实登记：`routes_checkpoint.py` 历史列表与 `evolution_service.py:241` 的按 memory_id 查询仍按各自口径排序（本票只钉 rollback 选取面——读者侧全序属独立面，tie 存量行在读者侧的显示序仍无契约，量级轻：显示序非写路径）。
- **rollback 复活撤回行收口——治理字段不随快照回拨 + 撤回态入口守卫 + 非 active 跳过重索引（票 36）**：rollback 把上一 checkpoint 的 `after` 整行快照逐字段 setattr 还原（`id`/`version` 已排除——票 23），但 `status`/`lifecycle_status`/`superseded_by`/`weakened_at`/`superseded_at`/`retired_at` 仍在回拨范围；而 `retract_memory` 撤回**不打 checkpoint、不 bump version**——撤回后最新 checkpoint 的 `after.status` 恒为撤回前的 `"active"`，admin 撤回的记忆可被非 admin 属主借 rollback 复活（探针实测 `ok=True` + FTS 重新召回），绕过 admin-only 的 unretract 门——**同一操作的撤销面有门（`unretract requires admin`）、旁路面无门**，击穿 ADR-0047 笔削四分法验收口径（「主张错误即全检索面禁用，不可自动复活；撤回后禁用命中=0」）。三处收口：①还原循环排除 `_LIFECYCLE_FIELDS` 治理字段集（内容与元数据字段照常还原——状态迁移只能走各自的门：retract/unretract/archive/知命状态机 ADR-0081，回滚是「内容版本回滚」不是「生命周期倒带」）；②撤回态入口守卫（同 correct_memory「retracted memory cannot be corrected」同款护栏）——`status=="retracted"` 直接 `{"ok": False, "reason": "retracted"}`，先于 CAS 认领**不留半写**（不写行、不追加 checkpoint）；③索引面跳过**只限撤回行**（收口轮自审修正——初版对全部非 active 行跳过，spec 轴审查发现 archive 语义是「FTS/Chroma **保留**（复原零成本）」而 unarchive 出闸只补缺失层不校验陈旧（`if not fts_present`/`if not vector_present`），跳过 archived 行会让「SQL=回滚后内容而索引=回滚前内容」的错位永久残留；修正后 archived 行随回滚内容重同步、回执 `index_skipped: "retracted"` 只在防御臂 b 层（守卫放开的未来形态）出现）——**票面修正**：立案时称「`sync_fts` 有 status 过滤兜底」，现场 `search_fts` 只 JOIN memoryitem 做租户/归属过滤、无 status 过滤（禁用在终闸），FTS 必须写侧守住「撤回即清索引」语义。同族面随本轮一并修：archived 倒带回 active（轻）、知命单向迁移被倒转（轻）——均由 `_LIFECYCLE_FIELDS` 排除集覆盖。回归锁：`tests/test_promoter_rollback_lifecycle.py` 五臂（形态照 test_promoter_rollback_vector.py：真 SQLite + 真 FTS5 + 真 Chroma + 3-gram 哈希 embed 替身只替外部面，retract/archive/rollback 全走产品入口；修复前 **4F/1P 真红亲证**：撤回行 `ok=True` 复活、非 admin 属主复活、archived 倒带、`'superseded'→'active'` 回拨；无辜臂绿锁防「把 rollback 整个禁掉」；红/绿日志 `.scratch/watch/logs/ticket36-{red,green,family}.log`）+ 向量家族 2P 零漂移 + 家族 88P + 终树终跑全量。`use_count/helpful_count/injection_count` 计数器回拨与票 23 的 RMW 面相邻，本轮不展开（随票 23 机制统一考虑）。
- **调度面配置缺口二项收口——consolidation 参数化开关 + REFLECT 察窗评估点补齐（票 34）**：①consolidation 是 scheduler 内唯一既硬编码时刻（`hour=19, minute=30`，注释自证「北京时间凌晨 03:30」——只对东八区宿主成立，破例 ADR-0002 零硬编码口径）又无 ENABLED 开关的定时作业，`CONSOLIDATION_ENABLED=False` 也关不掉；补 `CONSOLIDATION_ENABLED/CRON_HOUR/CRON_MINUTE`（默认 19/30/True 行为零变化）与 digest/reflect 同形，挂载加开关门、时刻读 settings，FROZEN_DEFAULTS + 司天面「调度」类目三键同步。②`REFLECT_OBSERVATION_WINDOW_DAYS/REQUIRED_RUNS` 注释承诺的窗口门槛在 lantai/ 内零消费者（唯一消费者是 standalone 发布准备脚本）——「配置显示有门槛、实际从不评估」；拍板**工单 (a) worker 自闸 / (b) 删参数皆否**（自闸死锁：察窗合格运行日由运行本身产生，跳过则永不累积、≥7 天停机后永久无法恢复——ADR-0027 自证「门禁测的是 cron 可靠性」需 cron 在跑才能测；删参数=撤在世立法：CONTEXT.md 察窗条目/脚本消费者/判据文本均在世），取 **(c) 评估点补齐**：`reflect_worker.observation_status` 新增（ADR-0027 四判据 + 日级 OR），每次 scheduled 运行真评察窗 → `logger.info` 留痕 + 回执 `observation` 键透出，未毕业**不跳过**（采样器职责，CONTEXT.md 立法语义＝观察窗触发校准不是闸 worker）——门槛自此每次运行必被评估、必可见（宁 visible 不静默）；评估失败 `logger.exception` 不阻断运行；ADR-0027 增补「实施现状」段（影响段承诺的 release_check 接线从未落地——现场亲证，消费点现状与不自闸论证如实记录；不扩 14 项分母）。回归锁：`tests/test_scheduler_config_gaps.py` 六臂（真 `start_scheduler` 注册逻辑 + 真跑 worker，仅线程壳按仓内既有范式替换，禁 mock 被测链路；修复前 **6F 真红亲证**：worker 回执无 observation 键实锤、CONSOLIDATION_* 注入点不存在实锤；判据双锁臂钉死脚本↔worker 判据一致——5 合格日 + manual/异常独立日，判据错则 7 翻车；红/绿日志 `.scratch/watch/logs/ticket34-{red,green}.log`）+ 家族 78P（脚本既有测试零漂移）+ 终树终跑全量。剩余风险登记票面 Answer：脚本窗口锚本地今日/日键 UTC 的既有形态、release_check 察窗项接线属独立发布门禁变更、察窗毕业的校准联动属后续裁决。
- **读侧时间语义漂移三处收口——aware 输入归一 + 报表本地日口径统一（票 32）**：读侧三处纪律漏网点（背景：锁定 sqlmodel 0.0.42 无 UTCDateTime，aware 绑定 SQLite 不做偏移换算，正确性全靠「写侧恒 UTC + 读侧显式归一」）。①`chenfeng.search_archived` 把 FastAPI Query 的 +08:00 aware `time_from/time_to` **直接绑定比较**——+08:00 墙上时钟被当 UTC 文本与库内 UTC 值比，北京用户查「11-01 全天」实际比较 [11-01 00:00, 11-02 00:00) UTC：北京 00:00–08:00 的归档记忆**静默漏掉**、次日凌晨的**混入**，双向漂移不报错不可见；两处绑定前过 `core/time.ensure_aware`（gouchen 同款先例，归一真源不重写），同文件 `evaluate_health` 两段手写归一对齐统一。②`qijuzhu.get_daily_chronicle`「今日」按 `utcnow().date()`（UTC 日历日）——北京 00:00–08:00 拿到**昨天**的纪事、显式传 date 也是 UTC 日窗；改本地墙钟日 + `core/time.local_day_window_utc` 本地日→UTC 窗口，加 `now` 冻结时钟注入缝（生产缺省实时，票 31 同款范式）。③`ops/usage.collect_usage` 每日桶 `func.date(created_at)` 按 UTC 日切——北京凌晨写入落前一天桶；改本地窗口起点 + 读值归一后按本地墙钟日归桶（弃 SQLite `localtime` 案：环境时区依赖宿主不可控不可测），缺日补零输出形状零变化。**三份报表（digest/起居注/用量）同日界由共享真源结构性保证**：`core/time.py` 新增 `local_day_window_utc`（与 digest_worker 私有版逐行同语义；digest 私有版零触碰，委托收敛登记票 84）；调用方文案同步（`routes_health` /usage docstring 的「单条 GROUP BY」已陈旧如实改「按本地日归桶」、MCP `mem_usage` description 补本地日口径）。回归锁：`tests/test_readside_time_semantics.py` 七臂（真内存库真引擎，禁 mock 被测链路；修复前 **5F/1P 真红亲证**：漏掉臂 `ids=[]`、混入臂混进次日凌晨、qijuzhu 显式日期漏本地凌晨端、默认今日缺 now 缝 TypeError、usage 桶 1!=2；naive 契约锁与 +08:00≡UTC 等价锁钉死不回退；红/绿日志 `.scratch/watch/logs/ticket32-{red,green}.log`）+ UTC 宿主三臂 skip 带由（offset==0 上本地日与 UTC 日重合现状即正确，`LANTAI_READSIDE_TZ_ARM_FORCE=1` 强制阀）+ skip 台账 7→10（共享标记变量形态对扫描器隐身，弃用改逐测试显式装饰器——首跑该形态被台账纪律自查拦截）+ 家族回归 + 终树终跑全量。工单红测字面种子矛盾如实申报：工单断言「11-01 18:00 UTC 必须命中北京 11-01 全天」与病灶语义矛盾（该时刻属北京 11-02），按正确语义断言不出现——禁把病灶编码进期望值。剩余风险登记票 84：本地日语义锚系统时区（UTC 宿主回到 UTC 日界）、digest 私有版待委托收敛。
- **每日盘点时间窗差一天收口——触发点移到本地日末 + 补跑锚定错过触发日（票 31）**：Daily Digest 此前 `DIGEST_CRON_HOUR=22`（UTC）= 上海**早晨 06:00** 触发，而报告窗口是触发时刻的本地日历日 `[D 00:00, D+1 00:00)`（`digest_worker._local_day_window_utc`）——北京用户早晨 6 点拿到的"日报"只覆盖当天凌晨 0-6 点数小时，前一天 06:00 之后到达的 18 小时数据在窗口语义上属于"当日"却永远缺席当日报告（触发时窗口刚开 6 小时；次日触发时又锚了新的一天）。引入面：`2e261055`（Ticket 03 Daily Digest 首版即早晨触发 + day=None 锚定，从未对齐过日界）。拍板**案 A（票面推荐）：cron 移到本地日末附近**——`DIGEST_CRON_HOUR` 22→**15** + 新立分钟位 55（UTC 15:55 = 上海 23:55，距日界 5 分钟，触发时窗口已基本走完、语义自洽；残留 5 分钟尾巴属结构性可接受——23:55-24:00 到达的数据由次日报告按其自身窗口语义自然承接）；分钟位单一真源 `_DIGEST_CRON_MINUTE` 0→55 且 **cron 注册补显式 `minute=`**（此前注册面漏传，APScheduler 按 minute=0 触发，该常量对注册面是死配置——改常量不动注册面修复即哑弹，AST 哨兵锁死）；启动补跑**锚定错过触发点所报告的本地日**（`_catch_up_daily_jobs` 此前注册裸 `run_digest_once` 锚启动时刻——昨晚 23:55 错过、今晨 08:00 启动，补跑会把启动日的半天数据写成"今天"的残报，错过的那日永远缺报告；现经 `_most_recent_daily_fire` 抽取（与 `should_catch_up` 同源单一真源）换算触发点本地日传 `day=`；`now` 参数注入缝对齐仓内冻结时钟范式）。窗口数学本体零改动（`[D 00:00, D+1 00:00)` 左闭右开本就正确——病在触发点不在窗口）。回归锁：`tests/test_digest_window_anchor.py` 7 例（真内存库真链路，禁 mock 被测链路；修复前 **4F/3P 真红亲证**：配置契约臂实证触发点上海 06:00、两段到达臂实证三粒种子仅 1 粒入报、注册哨兵 AST 实证 minute 漏传、补跑臂实证缺 now 锚定缝；消费端现状锁与全天/边界两绿锁按设计即绿；红/绿日志 `.scratch/watch/logs/ticket31-{red,green}.log`）+ 两段到达臂为配置自适应两段模型（post 为空即修复生效形态；触发点回退日间则两段丢失检查重新咬合）+ 非 UTC+8 宿主 skip 带由（宁 skip 不脏写；skipif 守卫带 `os.environ` 强制阀 `LANTAI_DIGEST_TZ_ARM_FORCE=1`——既满足 skip 台账环境谓词判据又给非 +8 CI 实跑出口）+ **skip 台账申报**（`test_skip_mask_inventory` DECLARED 6→7）+ 家族回归 + 终树终跑全量。首跑全量 3F 如实申报（均设计内拦截非生产缺陷）：未申报 skipif 被 skip 台账双锁咬合（申报 + 环境谓词判据）+ 新测试文件 ruff format 格式差——申报与格式化后终树复跑全量。剩余风险如实登记：触发点语义对非 Asia/Shanghai 部署宿主仍依赖系统时区（窗口锚系统本地日、案 A 只在 +8 宿主完全自洽——UTC 宿主上 15:55 触发距其日界尚有 8 小时尾巴），与 settings.py 既有 Asia/Shanghai 注释假设同源，登记票 84 一并裁决。
- **观测面保留策略不对称收口——retrieval_event/llm_call_event TTL 清扫 + scheduler 挂载 + 发布门禁保留对称性对账（票 29）**：三条观测链此前只有 `operation_logs` 有 `MONITOR_RETENTION_DAYS` prune（telemetry.py 范式点），`retrieval_event`（检索逐次落库，全库最高频写入表之一）与 `llm_call_event`（LLM 外呼逐次落库）**零行级清理无界增长**——观察窗计数（recall_report）/回执对账/usage 统计类查询随时面线性劣化，且不对称成形于 `315f3b87`（司天 v0.4 只给 operation_logs 配保留期）。现（`lantai/observability/retention.py` 单一真源）：①两表各配独立保留旋钮 `RETRIEVAL_EVENT_RETENTION_DAYS` / `LLM_CALL_EVENT_RETENTION_DAYS`（默认 30 天，不复用 MONITOR_RETENTION_DAYS——取证窗与观察窗语义不同源），TTL 下限**结构性钳制** `max(config, RECALL_MONITOR_WINDOW_DAYS)`（TTL 短于观察窗会让 recall_report 窗口计数 ADR-0027 静默失真——宁可钳制 + warning 留痕，宁 visible 不静默；司天 24h 降级率窗低于任何天粒度 TTL 结构性满足）；清扫 `DELETE WHERE created_at < cutoff` 严格小于（边界行保留，回归锁钉死防 `<=` 抖动）、幂等（二跑 0）、返回删除条数、删除留痕 `logger.info`、失败吞掉返回 0（清扫不反噬调度链）；②scheduler 挂载 `observability_purge`（每小时，镜像 kanhe_purge 形态）；③司天面 `collect_security_view` 增 `retention` 块（逐表行数/保留天数/最近清理时间，对齐 telemetry.stats() 范式——局部清理没有全局可见度=整体不可证）；④**发布门禁新增「观测面保留对称性」项**（判定 13→14 项）：以 `OBSERVABILITY_RETENTION_TABLES` 清单为真源逐表对账「settings 旋钮 ↔ 清扫函数本体 ↔ 生产挂载（scheduler prune_all / telemetry flush 自扫）」，缺一即拒发——后续新增观测追加表不进清单配齐三件套过不了门禁（未修复树经 stash 摘除生产面实测 **3 条可操作 issue 拦截**，`.scratch/watch/logs/ticket29-gateproof.log`）；⑤ADR-0084 增补 §2.7 立法：证据留存 = TTL 内（vetoed/missed 行与其它行同律过期删除，不为任何回执状态承诺「永留」）。operation_logs 既有 prune 行为零改动（现状对照臂锁）。回归锁：`tests/test_observability_retention.py` 13 例（真内存库直调清扫本体，禁 mock 被测链路；修复前 **12F/1P 真红亲证**——1P 为 operation_logs 现状对照臂；红/绿日志 `.scratch/watch/logs/ticket29-{red,green,family}.log`）+ 家族 248P + release_check 诊断清单/分母锁/桩表三处设计内同步（`docs/release-process.md` 上传前门禁 13→14 项）+ 终树终跑全量。剩余风险如实登记：清单是手工维护的申报面——新增观测表未进清单属新立案面（同票 80 对迁移链的边界）；环境依赖存量失败 `test_forgetting_gate_really_runs_and_pollutes_nothing`（无 `PYTHONIOENCODING=utf-8` 的独立跑时子进程 GBK 输出致 PASS 标记失配；全量套件环境内通过，stash 对照实锤非本票回归）——即票 30 病灶 A1（`release_check.py:1082` forgetting gate 探针）的测试面显形，票 30 收口（`isolated_probe_env` 钉 `PYTHONIOENCODING=utf-8`）后该保真测试在裸 shell 亦自动转绿，不另立票。
- **LIKE 通配与裸标点降级臂过匹配收口——三处 LIKE 点统一转义真源 + 裸字回退守门（票 28）**：LIKE 兜底臂此前对用户 token 零转义零守门——`hybrid.py` 裸字回退段在纯标点查询（如 `%`/`_`/`%%_%_`）时把通配元字符直进 LIKE 模式串（`LIKE '%%%%'` 全库命中，实测 5/5），`_` 是单字符通配（查询 `foo_bar` 把 `fooXbar` 当通配捞进），同病姊妹点 `shiyi.py`（`[\w一-龥]` 保留 `_`，裸 `_` 查询全表扫描）与 `chenfeng.py`（`contains()` 底层 LIKE 未转义）。拍板**转义而非剥离**（剥 `_` 会废掉「查询 foo_bar 应命中字面 foo_bar」的验收）：新增 `lantai/core/text.like_escape` 单一转义真源（`\ % _` 前缀反斜杠、反斜杠最先替换）+ `LIKE_ESCAPE_CHAR`，三处 `.like` 点统一 `escape=` 转义、chenfeng 用 SQLAlchemy 原生 `autoescape`；hybrid 裸字回退路径守门（逐字符碎片剥除通配元字符、剥完为空该臂空手——宁空不全库）；`fts.py` 两处兜底 catch 内联 `import logging` 统一模块级 logger。FTS5 MATCH 面零接触（`_bm25_keywords` 净化器无注入路径，票面探针实锤）。行为变化如实申报：纯通配/混合通配查询从「全库返回」变空手（宁空不全库）、含通配字面查询变字面匹配。回归锁：`tests/test_like_wildcard_escape.py` 8 例（真内存 SQLite + 真 hybrid/拾遗/尘封链路，embed 臂抛错复现降级形态；修复前 **7F/1P 真红亲证**，红样含全库返回实数 5/5；红/绿日志 `.scratch/watch/logs/ticket28-{red,green}.log`）+ `tests/test_fts_integration.py` 36P 零断言改动 + 家族 127P + 终树终跑全量。
- **MCP 约束面/描述面失真四处收口——承诺了没做与做了没承诺双向对齐 + 不可修项锁定（票 27）**：①`triage_auto_pilot` schema 承诺 limit [1,500]、conf [0.0,1.0] 而 handler 裸 `int()`/`float()` 强转零范围检查（limit=0/99999/conf=9 全部接受并直通 DB `.limit()`，同族 `triage_analyze` 有校验、同名参数行为不一致）——handler 补三参范围校验（报错文案对齐同族）+ schema 补 minimum/maximum 机器可读关键词；②`candidate_review`/`crystal_decide` 的 reject 分支服务层强制 reason 必填而 schema 广告为可选零描述——补条件必填注记（描述层说明，revive_consolidated 同风格）；③`qijuzhu_chronicle` 的 date/detail_level 在 days>1 分支被静默丢弃（票 07「假承诺」轻量版）——宁 visible 不静默：显式传参时回执体 warnings 含「参数未适用」标记 + description 注明仅单日生效；④弱广告搭车面（纯声明零行为变化）：五处裸 limit [1,500]、top_k [1,100]×2、recall_report days [1,365]、autodream_report limit [1,5000]、add.content minLength 10、conflicts/proposals/crystals status 机器可读 enum（handler 界逐一核实）。**不可修项如实登记**：domain 枚举跨工具不统一经消费端核对判语义差异非失真（muci/shiyi_pipeline 的 domain 是列等值过滤，"all"=静默空结果，现状锁钉死不得误加）。行为变化如实申报：triage_auto_pilot 越界参数从静默接受变 -32602（收口目的本身）、qijuzhu 多日分支响应新增 additive warnings 键。回归锁：`tests/test_mcp_schema_contract_face.py` 11 例（AST 结构化导航 schema-face 锁 + 真子进程隔离 LANTAI_HOME dispatch 行为臂 + 真库种子现状锁；修复前 **8F/3P 真红亲证**，红/绿日志 `.scratch/watch/logs/ticket27-{red,green}.log`）+ 家族 185P/106P + 终树终跑全量。
- **MCP 广告面反向缺口五处收口——治理杠杆/身份链在 tools/list 完整可见 + 哨兵双向化（票 26）**：MCP 消费面此前可私藏未广告参数（广告→消费方向票 07 已收口，消费→广告方向零哨兵）——`search.force` 可静默旁路相关性闸、`cehou_observe.mock_days_observed` 可伪造观察窗（REST 面同参已声明而 MCP 漏跟的双面不对称）、`user_id` 经 `_principal_from_params` 中心消费 62 工具 schema 未声明（归属链在广告面不可达，宿主按 tools/list 开发即系统性漏配）、`search.session_id/shared_scope/task` 有行为无广告、`memory_archived_search.scene_id` 未广告别名。现：force 与 mock_days_observed **声明对齐 REST 面同参同语义**（description 注明白名单豁免语义），search 三参与 user_id（62 工具，共享常量 `_USER_ID_PARAM` 单一声明源；4 个不消费工具不声明防虚广告）全部入 schema，scene_id 别名删消费点（两名一参只文档一个）；哨兵双向化——`tests/test_mcp_schema_advertise_audit.py` AST 结构化导航提取（绝不 ast.walk：嵌套 items.properties 覆写顶层键集实证陷阱），消费⊆广告∪豁免 + 广告⊆消费∪豁免双向断言、全局豁免表硬编码（user_id 中心消费，扩表必改测试显式留痕）、消费驱动 user_id 声明全覆盖锁、治理杠杆语义注记锁、别名删除锁、行为锁（force=false 真 gate 照拦 / user_id 透传落库，真内存库）+ 真子进程 tools/list 冒烟。回归锁：修复前 **5F/3P 真红亲证**（红/绿日志 `.scratch/watch/logs/ticket26-{red,green}.log`）+ MCP 家族 185P + 终树终跑全量。
- **模型索引迁移漂移收口——迁移链 v40 幂等索引同步（单一真源 = 模型声明）+ 发布门禁升级库形态探针（票 25）**：索引此前只有两条出生路径——表首次创建时 create_all 随表落索引（SQLAlchemy 对已存在的表整体跳过，索引不补）与迁移链手写 CREATE INDEX，全仓无「模型索引 ↔ 库索引」对账机制——升级库（remembrance.db 形态：user_version=0、身份时代之前的存量表）经迁移链 ALTER 补列后，身份列以**无索引形态**进入每条检索的 WHERE 过滤（模型声明 156 索引 vs 链手写 27 组，立案探针 147/27/122 实跑）；且发布门禁的 schema 探针只比列存在+NULL 约束、只探**全新库**——全新库 create_all 天然带全部模型索引，升级库漂移在门禁下天然不可见。现：①迁移步 `_migrate_v40`（TARGET_SCHEMA_VERSION 39→40）遍历 `SQLModel.metadata` 全部声明索引（单一真源，此后索引增减只改模型、手写增量归零），表存在才建、缺失补建逐条 logger.info 留痕（大表首次建索引持写锁，运维可见首启变慢原因）、已有跳过；列缺失（链上无对应 ADD COLUMN 的既有列维度缺口，另票）INFO 跳过不告警——老库前向迁移零 warning 契约保持；单索引真失败 warning 隔离不阻断链。②门禁探针补**存量库形态模拟**（姊妹文件 create_all → 剥离全部非 autoindex 索引 → memoryitem 身份列删除 → 真 apply_migrations 全链）→ 索引漂移对账 + upgrade 库 user_version 对 TARGET 核对（迁移未应用拦截）+ 二次 apply 幂等核对（重复应用拦截），三维度接线为 gate issue（非零退出直接输入）——缺 index、版本不匹配、schema 不匹配、迁移未应用/重复应用全部被拦截。回归锁：`tests/test_index_sync_migration.py` 3 例（升级库形态主臂 + fresh 现状锁/幂等 + 同名表占位注入的真实失败隔离，修复前 **6F/2P 真红亲证**）+ `tests/test_release_gate_schema_drift.py` 5 例（三维度接线 payload 单测 + 端到端探针收敛绿锁）+ 迁移族 90P + 终树终跑全量。剩余风险如实登记：列维度覆盖缺口拆票 80（retrieval_event 观测五列等）；链外 8 个手写索引名留观。
- **幂等覆盖缺口收口——勘合令牌三处原子化 + memoryitem 内容键部分唯一索引（迁移 v39）+ feedback 重放勘合票据（v0.68 票 24）**：幂等键此前只护潜移异步摄取一条链，其余写面重放即多写。①`_take_over` 过期接管是「SELECT 令牌复核 → Python 判 → ORM 主键 UPDATE」三步——令牌比对只在 SELECT 里，两并发接管都读到旧令牌都判赢 → 同键双执行；姊妹窗：`settle` 的落账 UPDATE/DELETE 与 `release` 的带令牌 DELETE 同按主键定位，接管插在 SELECT 与落账之间即覆写新主回执或删新主 claim（重试双执行）。现三条写路全部收敛**单条条件语句**（`WHERE 主键三元组 AND created_at=旧令牌`[+ `state=扫描值`]，写阶段第一条 DML 对最新已提交状态求值）+ rowcount 判胜负——并发败方如实 pending/gone，不再覆写新主；state 守卫挡迟到 finalize 的 state 前移（settle 不改 created_at，仅令牌挡不住）。②`POST /memory/add/raw` 与 mem create_skill 的去重是「SELECT(key=sha256) → embed（网络往返）→ INSERT」且 `MemoryItem.key` 无唯一约束兜底——并发同文两行 verbatim + 双份向量/FTS。现补 `(memory_type, key, user_id)` **部分唯一索引**（`WHERE key != '' AND status='active'`，与去重 SELECT 口径逐字对齐：default="" 无键行、NULL 属主老行、归档/撤回行均不受约束）+ 迁移 v39 （存量对账：同键 active 组留最早行——同键行内容逐字节相同，最早行持最长引用史；NULL 属主同键组不参与去重只清点留痕，宁 miss 不脏写；TARGET_SCHEMA_VERSION 38→39）+ 写路撞约束败方回滚重读收敛（幂等回执形状与串行去重命中一致，不再 500）。③`POST /evolution/feedback`（REST/MCP 双入口收敛点 `record_feedback_entry`）无去重键——宿主/网络层重放三计数各再加一次（考功/遗忘的输入，虚增即「高频低效」误判面）。现挂**勘合票据**：自动指纹键（整个请求体+viewer 的 sha256），重试窗 LEASE_SECONDS 600s——窗内同体重放如实 ack 不再计数，窗后同文再发 = 新事件照常计数，不同 query/helped = 独立事件；选票据而非原子递增（票 23 F1 的原子递增解决并发丢失，对重放无能为力）；服务层 `record_feedback` 保持纯函数（票 23 F1 并发契约与直调零扰动）。④`POST /retrieval/backfill` 核验判 covered（票 23 F2 的认领+差集+veto 守卫已关重放/并发/veto 三轴）；`POST /add` 与裁决路由按工单定级 needs-info 只登记不实现（重放重复候选语义待维护者拍板；票 22 条件 UPDATE 已使重放裁决如实拒绝）。并发行为收紧面（如实声明）：勘合接管/结算/释放的并发败方从「覆写新主」变 pending/gone；raw/skill 并发同文败方拿到胜者行 id（dedup=True），不再双行；feedback 窗内重放得到 replayed ack 不再虚增计数。回归锁：`tests/test_idempotency_receipt.py` 9 例新增（接管双赢/迟到结算覆写/state 前移/replay 语义锁×3/raw 双行——跨 session 交错注入，垫片先调真实现再经第二 Session 真实提交，修复前 **4F/35P 真红亲证** + state 守卫 Edit 回退法真红）+ `tests/test_raw_memory.py` 1 例（并发同文单行 + 单份向量）+ 家族门禁三批 400P + 终树终跑全量。顺带发现登记票 79：raw REST 路由 principal 未透传，票 20 跨属主去重守卫在 REST 是死代码。
- **计数器与整行快照读-改-写丢更新收口——11 病灶三层机械修 + CoreMemoryBlock 唯一约束迁移 v38，并发增量不再被旧快照整字段覆盖（v0.68 票 23）**：计数器/快照更新链此前是「读字段 → Python 计算 → 整字段回写」——pysqlite 惰性 BEGIN 下两线程各持旧快照各写各的，后提交者整字段覆盖：遗忘可归档刚被反馈/保活救活的记忆且归档无 undo（W1）、同一回执重放双计数注入且 veto 终态可被复活（F2a/b/c）、并发反馈丢 use_count/helpful_count/importance 递增（F1）、考功分钟级评估后按旧快照绝对覆写 importance/tier 抹掉运行中落库的反馈（F6）、去重直合 bump 吃掉并发反馈 +0.2（F3）、候选 defer/undo 按旧 due 抹并发延期审计（F4a/b）、CoreMemoryBlock 并发首写双插幽灵行（F5a）且封存/put 版本重号（F5b）、gate 降权把刚被表扬的记忆打成低分并落虚假 checkpoint（F7）、并发更正整列抹掉更正史 + version 同值重号（S8）、知命转移覆写已推进的生命周期（S7）、rollback 整行旧快照还原抹掉并发写（S7″）、terminal merge 快照覆盖（S7′）。现收敛三层机械修（认领全部落写阶段第一条 DML——惰性 BEGIN 此刻才开写事务、WHERE 对最新已提交状态求值）：①纯计数器/时效列 → `cas.atomic_update` 单语句原子 UPDATE（库端表达式 `coalesce+1`/`min-max` 夹值，`synchronize_session=False` 显式钉死）；②判定类 → 条件 UPDATE 写时重验扫描期判定输入（rowcount=0 → 跳过/冲突如实报，宁 miss 不脏写——遗忘五列重验、考功 importance/tier/status、gate importance、回执 receipt_status、候选 status+扫描期 due）；③JSON 追加/整行覆写 → 版本前置 CAS（`WHERE version=扫描值`）+ 有界重试（3 次即时重读、刻意无 jitter——SQLite 单写者下重读必见胜方提交，random sleeps 反引非确定性）：纠错史重拼、rollback/merge 入口认领（败方 ok:False/409 如实拒绝）、知命 `cas_update WHERE lifecycle_status=from_phase`。配套：`CoreMemoryBlock (block,namespace,user_id)` 唯一约束（模型 `__table_args__` + 迁移 v38——去重留 version 最高行、NULL 属主不参与[SQLite UNIQUE 对 NULL 互异]、TARGET_SCHEMA_VERSION 37→38），两处并发首写撞约束败方重读转更新路收敛；`put_core_memory`/石室封存版本库端自增。并发行为收紧面（如实声明）：并发败方现在收到 409（merge）/CandidateStateConflict（defer/undo）/ValueError（知命转移）/跳过留痕（遗忘、考功、gate、保活）或 `ok:False`（correct 耗尽重试、rollback），不再静默覆写成功；`_dedup_merge` 不再收 detached 实例（复活提交会把陈旧快照冲回库——恰是本票消灭的 RMW），调用方须同 session 取行（生产链路即此形状）。回归锁：`tests/test_counter_rmw_lost_updates.py` 16 例（跨 session 交错注入——垫片先调真实现再在返回前经第二 session 执行 B 侧完整真实流程，缝全部钉在 A 首条 DML 之前，被测链路零 mock、单线程零 sleep 确定性；修复前 **16F/0P 真红亲证**，红/绿日志 `.scratch/watch/logs/ticket23-{red,green}.log`）+ 家族门禁六批 653P（门禁抓到并已修：TARGET_SCHEMA_VERSION 未同步、迁移 v38 老库 warning 违反「健康库零 warning」契约、put_core_memory INSERT 路提交过期后 `model_dump()` 空 dict[e2e 回归]——两路汇合统一 commit 后 refresh）+ 终树终跑全量。残留登记（票面 Answer）：票外同族面拆票 78（tanyi.py:202 探颐消解丢用户纠正 P1、record_ops revive/unconsolidate 版本重号、lifecycle.py 直写族、sanxing/probing/consolidation/promoter 状态覆写族）+ late_correction.py:88-91 零调用方死代码登记不修。
- **sqlmodel 版本钉死 0.0.42 与全仓时区防护注记如实化，正确性论证不再指向不存在的库级防护（v0.68 票 33）**：pyproject 的 `sqlmodel>=0.0.22,<0.0.48` 上界放进 0.0.47 本身——该版起 UTCDateTime 对 naive 写库直接 raise、读回值变 aware（0.22.x CI 红 37 例的机制），`uv lock --upgrade` 一越线即契约反转，而全仓 ~30 处注释仍以「UTCDateTime 拒 naive」为现在时依据，该防护在锁定的 0.0.42（datetime 字段映射裸 DateTime）从未生效，今天不炸纯靠「写入方恒 UTC」纪律。现：上界收紧为 `>=0.0.42,<0.0.43`（uv.lock 同步重锁，仅 requires-dist 一行变化、包版本不动），28 处注释位改述真实契约（生产 10 + 测试 18）——0.0.42 无库级防护（naive 也静默落库），恒 aware UTC 靠约定（core/time.py 单点）+ 读侧 `_ensure_utc` 归一，升级 ≥0.0.47 前须先清 naive 写入面；promoter 回滚快照注记顺带把报错形态按版本如实分列（锁定 0.0.42 报「SQLite DateTime type only accepts Python datetime and date objects as input」，0.0.47 才报「'str' object has no attribute 'utcoffset'」——原文把后者的报错串当成了现行行为）。回归锁：`tests/test_sqlmodel_datetime_contract.py` 三臂（版本反转哨兵——≥0.0.47 进入环境先红；真内存库落盘形态锁——aware-UTC 落盘为无偏移 UTC 墙上时钟文本；注释如实锁——core/time.py 不得再以现在时表述指向不存在的防护）。

- **裁决/认领状态机竞态收口——全族条件 UPDATE（CAS）化 + 手动触发互斥，并发败方从静默覆写变 409（v0.68 票 22）**：裁决/认领状态机（候选 pending_review、提案 pending、冲突 open、论文 new|retry）此前全部是「SELECT 快照 → Python 判定 → 无条件 ORM 写回」——pysqlite 惰性 BEGIN 下纯 SELECT 不进写事务，判定与写回之间零隔离：reject 后 approve 可复活已拒候选并为其生成提案（S1）、提案可被 decide 内联 apply × 定时补跑 × 自动应用双落库（add 双平行记忆 / update checkpoint 重号，S3）、reject 持旧 ORM 对象可把已 APPLIED 覆写回 REJECTED（内容已写库账面显示拒绝，S2）、TTL 归档可抹掉并发 defer 的审计四字段（S5）、冲突可双人并发裁决后写覆盖先写（S6）、论文批可被定时作业 × ingest 触发双 run 认领（双份 LLM 生成，W3）。现收敛 `lantai/storage/cas.py` 单一真源（`UPDATE ... WHERE 主键 AND 期望态` + rowcount 守护 + `synchronize_session=False` 显式钉死——ORM 默认 evaluate 模式会拿 stale 属性回写正是原病灶的后门），认领放写阶段第一条 DML（惰性 BEGIN 此刻才开写事务、对最新已提交状态求值，胜方回滚认领一并撤销无中间态残留），LLM/embed 网络调用全部留在认领之前（先无事务评估、后短事务条件提交）；parameters 批准路径的既有 CAS 同步收敛进单一真源。并发行为收紧面（如实声明）：并发败方现在收到 409（decide/resolve/claim）或被 worker 跳过并留痕，不再静默覆写成功；已消费候选/提案/冲突不可二次进入（重复 propose/apply 按设计被拒）。放大器收口：`POST /evolve/run` 改经 `run_worker("evolve")` 同名互斥（cron × 手动并发只成功一个，冲突 409，响应形状 `{"ok": True}` 不变）；`POST /evolution/consolidate` 撞上运行中的周期映射 409 + 沉潜周期入口非重入 Lock 单飞（不走 worker 注册表是刻意的——`principal=ctx` 归属收窄不放宽，且 cron 直调绕过路由级互斥故锁在周期入口）；`run_evolve_once` 自身非阻塞单飞。回归锁：`tests/test_adjudication_state_machine.py` 12 例（跨 session 交错注入——垫片先调真实现再在返回前经真实公开 service 函数执行 B 侧完整真实流程，被测链路零 mock、单线程顺序执行无 sleep 无超时依赖；修复前 **10F/2P 真红亲证**）+ 家族门禁四批 717P + 终树终跑全量。残留登记（票面 Answer）：跨进程互斥（MCP 第二进程，进程内 Lock 挡不住）、reflect 双跑、WAL 决策、唯一索引兜底、机制 ADR 文档化——均 ADR 级单独立案。
- **沉潜折叠碎片向量残条收口——折叠 commit 后逐碎片删向量，与 promoter 同语义对齐（v0.68 票 21）**：`consolidate_cluster` 直写路径此前只删碎片 FTS 行不删向量（`sync_fts(s, m.id, None)` 后直接 commit）——与 promoter consolidation apply 分支对碎片的「status + 删 FTS + 删向量」三件事（promoter.py:518-524）直接矛盾，自证遗漏非设计。碎片向量与主记忆语义近同（主记忆即碎片合写），向量通道里 N 条碎片幽灵与主记忆竞争 `fetch_n` 名额，`_query_items` 按 `status=="active"` 静默吞（无日志），极端时主记忆自己的向量被挤出预算——刚沉潜完的主记忆在向量通道欠召回（预算污染 + 静默，非错误召回：行在、状态滤掉）。现（最小修复面）：`s.commit()`/`s.refresh(master)` 之后逐碎片 `delete_memory_item(fid)`，单条失败 try/except + `logger.warning` 留痕（含碎片 id），不中断、不回滚已提交的折叠——「SQL 先 commit、向量后删」仓内统一次序（routes_terminal.py:381-385、promoter.py:842-843），失败残条可见且可恢复（检索终闸 status 过滤兜底、复职路径重同步可重建）；FTS 时序、master 索引块、复职路径、promoter 零接触。回归锁：`tests/test_consolidation_vector_cleanup.py` 4 例（真内存库 + 真 `consolidate_cluster` 全流程 + 真 FTS；向量库记账替身只替外部存储副作用面；修复前 **2F/2P 真红亲证**——主臂红样 `delete 调用缺 ['mem_frag_01','mem_frag_02','mem_frag_03']（实际 []）`、失败留痕臂红样 `记录 []`；复职回归锁与假绿防线[真库断言 consolidated + FTS 已清]现状即绿如实申报）。残留登记（票外，与票 20 同根因）：修复只堵增量，存量已产生的碎片向量残条仍无人清扫，归并「向量↔SQLite 对账器」立案面。
- **终端合并三面索引孤儿收口——FTS 同事务 + 向量删源/重嵌 + 边清理 + supersedes 边同事务收编（v0.68 票 20）**：`POST /terminal/merge` 此前只动 SQL 面（改 tgt 正文 + 删 src 行），三面派生索引全部失同步——全仓改文路径普查（correct_memory/tanyi/probing/promoter/rollback）全部双层同步，merge 是唯一例外。①删 source 零配套清理：向量残条永活（`vector_store.search` 照常召回 src、`_query_items` 按 active 查无此行静默吞，每次检索白占 `fetch_n` 名额且无日志）、FTS 残行（`fts_has_row` 在场核查误报面）、src 双向 `MemoryEdge` 孤儿（下游 graph/edge_service 虽判不可见当不了真，admin 链展示露死 id）；②改 target 零索引同步（幽灵正文）：SQL 正文已是合并文而 FTS 行仍存旧文——按合并进来的 src 词面检索搜不到合并语义，向量 embedding 仍旧语义（按旧文召回、返回新文）；③supersedes 边走 `create_edge` 独立 session 即刻 commit、先于合并 commit，且被 `contextlib.suppress(Exception)` 包裹——合并 commit 失败回滚留两行 active 的幽灵 supersedes 边（`_apply_supersedes_order` 据此降权 src）、反向建边失败血缘缺失无痕。现：`sync_fts(tgt, 合并文)` + `sync_fts(src, None)` 同事务（ADR-0008，失败随事务回滚）；src 双向边清理（合并血缘 supersedes(tgt→src) 保留、其余删除——implement 轮边口径裁定「纯拓扑」：孤儿边端点已删、证据价值为零）；supersedes 边 in-session 构造 + dup 查重收编合并同事务（promoter 同款范式），去独立 commit 与 `contextlib.suppress`；向量同步 commit 后（embed 不占数据库事务）`delete_memory_item(src)` + `sync_vector_upsert(tgt)`，失败如实回报 `warnings`（correct_memory 形态；响应增 `warnings` 键为 additive，现无消费方无契约破坏面）。不动獬豸闸、不动 ACL/血统（`repoint_facts` 原位保留）。回归锁：`tests/test_terminal_merge_index_orphan.py` 7 例（真内存库 + 真路由函数直调 + 真 FTS5 表；向量库记账替身只替外部存储副作用面，索引终态全走真库/search 级断言；修复前 **5F/2P 真红亲证**——幽灵正文/向量残条/孤儿边/重复建边/幽灵 supersedes，红样精确 `('mem_tgt','mem_src','supersedes')`；两把现状锁[獬豸 422 拒臂零写入 + ACL 403]如实申报非红转绿）；terminal 全家族 32P 零破。残留登记（票外）：全仓无向量↔SQLite 对账器，存量已产生的向量残条/幽灵正文无人清扫，建议下一窗口立案（与票 45 FTS 重建同族）。
- **全局动作路由身份守卫收口——六路由守卫/透传归位 + require_admin 单一真源，admin 仅 env key 契约如实声明（v0.68 票 19）**：`app.py` CORE_ROUTERS 的 router 级 AUTH 只做**认证**（无凭证 401），六条全局动作路由「已认证、未授权」——任何持 key 者（含受限 Bearer user 与 DEV MODE）可：直跑全表演化周期（`POST /evolve/run`，LLM 批量消费 + 全库 tier/importance 写）、以 `SYSTEM_VIEWER` 系统身份驱动任意全库 worker（`POST /workers/{name}/run` 与 `POST /monitor/workers/{name}/run` 双挂载各有独立 UI 消费面，后者收 ctx 即弃——同名互斥只防并发不防滥用，循环调用可饿死 scheduler 同名作业；复查补刀 `POST /ingest/run` 与 `/workers/ingest/run` 触发**同一** `run_ingest_once`——平行 URL 不挂闸即整体架空守卫）、读全局审计/统计报告（`GET /evolution/kaogong/report` 零身份；立案外同族姊妹 `GET /kaogong/report` 已透传但 `KaogongEngine.get_report` 读全局 `_LATEST_KAOGONG_REPORT` **不消费 principal**——`details[].fenced_content` 携带记忆明文全文，且空报告态 user 身份会触发**用户视角全表写周期**并把全局快照覆写成 user 视角，票面推荐「透传」经实测判假修取 require_admin；复查补刀 `GET /retrieval/recall-report` 零身份调 `recall_report(days)`——服务本就按 viewer 收窄（readside-gaps/05）、MCP 透传而 REST 丢弃，服务收窄路由绕过＝跨属主统计投毒面）。现（`5ce463cd` + `5f4b46f7`）：① `require_admin` 自 routes_admin.py 收敛 `lantai/core/auth.py` 单一真源（三路由模块 cross-import 会拖 psutil 形成 api→api 横向耦合；routes_admin re-export 保 import 路径），严格 `role=="admin"` 与 `Principal.is_admin`（含 system，service 层口径）两语义并存原地注明；② 六路由挂闸/透传归位——worker 触发四路挂 require_admin；`/evolution/kaogong/report` 只挂闸不透传（空态保持 `skipped_no_principal` 安全空转现状锁）、`/kaogong/report` 守卫替换身份依赖（透传保持既有契约）、`/retrieval/recall-report` 透传 principal（服务收窄面，与 MCP 同口径）；③ runbook 两处 worker 触发指引补认证说明。已知契约如实声明：admin 仅 env key（`p0-auth-entry.md:68` Out-of-Scope P1 登记，`ApiKey` 表无 role 列）——受限 Bearer user 与 DEV MODE 对守卫面 403 与既有 admin 面同构，角色真正可赋值是登记在案的延后项。回归锁：`tests/test_global_action_routes_identity.py` 17 例（TestClient 真链 + **注册层结构锁**双形态兼容[懒挂载 `_IncludedRouter.original_router` / fastapi ≥0.136 扁平化]含阴性对照——新增路由漏挂守卫即红；admin 真触发臂与 401 认证层双臂为现状契约锁；修复前 12F/3P 真红亲证，补刀两臂修复前 8F 真红）。既有四钉点随行为变更补 admin 真链认证（断言意图不变）：test_e2e evolve_run、test_monitor worker 422、test_kaogong report 流、test_kaogong_api_smoke seam2。**复查收尾轮（子项 5 拍板收 admin）**：参数裁决面三路由收紧——`POST /param-suggestions/{id}/decision`、`POST /param-overrides/{id}/rollback`、`POST /param-suggestions/{id}/regenerate` 此前 `ctx.user_id` 仅作审计归属，accept/rollback 实为**全局运行参数变更**（ParamOverride revision 链 + settings 原位热换），多 key 部署下任何 per-user key 可跨属主投毒全局检索配置；MCP 零对应工具（无跨入口漂移）、全文件零「有意开放」声明 → 判收 admin（备选「声明性现状锁」会把未声明的意外固化为契约，弃；读面四路由维持 `get_current_user`）。独立复查六项现场验证零阻塞：注册层结构锁阴性对照**实操验证**（临时删守卫→真红→恢复绿）、get_report 假修裁决补**直调实锤锁**（零 mock 真调 `get_kaogong_report(principal=user_a/b)` 断言同一全局快照对象 + 他人 `details[]` 明文照见，锚 `cognition/kaogong.py:336-340`）、X-Tenant-Id 伪造头核查无新破窗（头只入 tenant/agent/session 字段，role 三路来源封闭）、管理面全签名重扫无第三漏网（`/stats` 免认证 count 级遥测登记票外）。回归锁扩至 **21 例**（参数判别对 ×3 + 直调实锤；子项 5 修复前 **9F/12P** 真红亲证）；test_param_api 五处决策/回滚钉点 admin 真链化（断言意图不变）；DEV 控制台参数裁决按钮随之 403（与守卫面同构代价，已声明）。
- **header scope null 半召回塌缩收口——六处租户/会话过滤补 NULL 半边 + session 孤儿语义摘除 + admin 豁免对齐（v0.68 票 18）**：`auth.py:124-126` 把 `X-Tenant-Id`/`X-Session-Id` 请求头直读进 `Principal`，而 FTS/BM25/LIKE 六处 tenant/session 过滤是严格等值、缺 `OR IS NULL` 半边（`fts.py` search_fts/search_fts_bm25 四处 + `hybrid._like_cb` 两处），带头调用把 NULL 属主老行（真实库 96.8% 主体形态）在关键词通道整批滤掉——向量降级（本仓明确支持的降级面）时关键词即全部召回通道 → 半召回塌缩；向量在场时两通道可见集相左，结果随 embed 服务健康度漂移（立案探针实锤：带租户头 LIKE 臂整臂返回 `[]`、修后同头同问句向量臂把行补回来）。现（`17b849bf`，最小面对齐终闸 `_query_items` 既有口径）：① `fts.py` 新增 `_tenant_filter_clause` 单一真源（`OR IS NULL` 半边，对齐 `_query_items:665-668`），两 FTS 函数共用——修前各自手写正是「改一处漏一处」的家底；② session 收窄三处摘除（终闸/向量通道均不过滤 session，FTS/LIKE 的收窄是与终闸相反的孤儿语义，implement 轮裁决）；③ admin 豁免对齐（`_like_cb` tenant 臂收进 admin/system 豁免守卫 + 两 FTS 外层守卫补 admin 全豁免——修前带头管理员在关键词通道比向量通道看得更少）；④ 修失效注释引用 `has_short_tokens :877→:1176`（注释级零行为）。隔离语义一字未动：跨租户非 NULL 行仍阻断。MCP 面不受影响（`_principal_from_params` 恒 `tenant_id=None, session_id=None`）。回归锁：`tests/test_header_scope_null_half.py` 15 例（真内存库 + 真 `hybrid_search` 全链，形态镜像立案探针；修复前 **13F/2P 真红亲证**，日志 `.scratch/watch/logs/ticket18-red-before.log`）——带头 NULL 行可见（fts/bm25/LIKE/降级全链四臂）、跨租户隔离现状锁、通道一致锁（同头同问句向量在场 vs 降级召回集一致）、admin 同宽锁、连续换头无污染锁、fixture 落库真值锁（防 fixture 带值空转）。门禁：该锁随全量套件进 CI（`.github/workflows/tests.yml`，PR + push master 双闸）与发布全量测试门禁。
- **票 03 审计票外项六条清仓——四修两挂账（v0.68 票 17）**：逐条核实出分诊矩阵（must-fix 4 / needs-info 2），子项 2 病位实测翻案（venv 3.11.15 判据级探针：multicast/unspecified 两层判据一致无漂移，真洞是 **CGNAT 100.64.0.0/10**——safety 以 `not is_global` 拦、arxiv 四标志放行，SSRF 纵深内层松于外层）。①arxiv IP 边界判据对齐 safety 并叠加原保守面（`53471071`，is_reserved 段不因对齐放开，宁严勿松）；②dialogue extract 臂摘除 X-06 复制 mode 分支，两臂统一无条件委托 `route_secret_hits` 单一真源（`d49f9676`，三态行为逐模态等价）；③dialogue 事件时间提取异常臂去静默——`except Exception: pass` 补 logger 留痕 + `event_time_extract_failed` 标记（`4f6dd315`）；④RSS/arxiv adapter 组装冒烟补齐——真 feedparser 对真实样本全链首覆盖，hash 双口径断言按现状锁定供存量拍板对照（`38b47d19`）。挂账待拍板：hash 口径统一（存量迁移语义）、ingest worker 逐 doc content_hash N+1（探针实锤 1:1 点查，批 10-50 无实测痛点）。回归锁：`tests/test_ingest_adapter_smoke.py` + `tests/test_ticket17_dialogue_strays.py`，全部红→绿实录（spy 仅记录并委托真实现；getaddrinfo/fetch/httpx 替身=网络边界）。

- **离线性能基线工具两处测量保真/口径缺口收口（v0.68 票 12）**：①`--only` 拼错场景名曾**静默跑零场景、exit 0、照常产出「成功」基线报告**——`main()` 现在任何跑批前校验场景名 ⊆ `SCENARIOS`，未知名 argparse error 非零退出并列出全部合法名（合并闸门项）；②`--profile` 对写场景曾**重放 timed 轮已摄取内容**（`make_scen_*` 每次新建 `_unique_call` 闭包、计数器从 0 起），重放被 `rawdocument.content_hash` 在场复用分支吸收、不产生文档 INSERT，摄取热点被系统性低估——写场景唯一序号流改挂 `BenchContext`（按场景名一条流，`seq_state`），profile 轮续接 timed 轮继续新内容不重放；`ingest_fastpath` 满足该验收标准后进 profile 列表。顺手清理：替身绑定还原改哨兵判（区分「原本不存在」不留 None 幽灵）、死导入 `sync_fts`/`timezone`、`run_scale`/`sanity_check` 双重加载 shell_hook、极小档 hit 查询集为空的裸 IndexError 改断言留痕；`run_scale` 增 `workdir` 测试缝（真库留点供回归锁断言）。红→绿全程实录（Edit 临时回退法）：缺口 1 回退 `seq_state` 后三把重放锁 4 例全红（真库行数 8≠18、序号重放）→ 恢复转绿；缺口 2 移除校验后未知名臂真红（main 静默返回 0）→ 转绿。回归锁：`tests/test_perf_bench_smoke.py` 增 5 例（不 mock，真库断言）。

- **獬豸闸二阶写入面四处补闸——LLM 提炼再分发面不再裸奔（v0.68 票 14）**：`assert_no_prompt_injection` 既有 29 处挂载的普查缺口——已过闸的对话碎片载荷经 LLM 合并/提炼后以**新记忆身份**回到全部检索/注入面的四个二阶写入点，按面分流处置（宁 miss 不脏写）。①consolidation 合并文本：闸落在 `consolidated_content` 提取后、master `MemoryItem` 写库前，命中单簇跳过（碎片原状保留零改动）+ `MemoryCheckpoint` 审计标记（伪 id `cluster_consolidation_xiezhi_blocked`，与直写指纹口径 `cluster_consolidation` 刻意区分防污染审计判据）；②evolution promoter apply：单咽喉点覆盖 add/update/merge/consolidation 四个内容写分支（deprecate 排除——其 patch 从不写库），命中落 **REJECTED 终态**不抛异常（evolve_worker 批循环无逐条保护，raise 会炸整批并让 APPROVED 池活锁）；③`POST /cognitive/observe` 自由文本直写：断言式 ValueError → 422（routes_memory 既有范式；复核全仓无自动调用方，不打断自动链路）；④`mem:create-skill` 单点挂闸（MCP ValueError → -32602），两处自动汇流点（crystal approve / 同袍晋升）catch 转 skip+留痕 `xiezhi_blocked: True`，catch 收窄为仅 `injection guard` 前缀（非獬豸 ValueError 不误标）。误拒红线：guard 26 例红线夹具五形态（Markdown 表格/错误日志/INI/裸 `<system>` XML/全角标点）× 四面逐一放行锁。回归锁：`tests/test_xiezhi_second_order_write_faces.py` 24 例（真内存库直调、payload 复用 test_injection_guard 夹具、绿臂含 payload 反查零行 + 正常对照落库 + 自动面不拖停臂；四面各自真实红基线 4F+1P/4F+1P/1F+2P/4F+3P 后转绿）。**续（`bbcd7e2f`）**：全量首跑 6F 实锤 staged 六段评测流水线的 store 段走生产 `apply_proposal`，语料 f1 围栏逃逸探针必须以注入载荷形态入库才能度量下游 `neutralize_fence_escapes`——面 2 闸把它拦在 store（fence_escape 锚点 redirected=store、inject 段 0 样本）。修法：`apply_proposal` 增 keyword-only `xiezhi_bypass=False`（默认生产语义零变化），staged store 段显式传 True + 仪器注释，不弱化语料不静默放行；三 staged 家族 6F→绿即端到端锁。

- **REST/MCP 闸门拦截轮原话不再落库——写入即脱文，三入口口径归一（v0.68 票 11，ADR-0084 §2.5 增补校准）**：ADR-0084 §2.5 边界句「gate 判 `needs_memory=False` 不落事件」只对 shell_hook 成立（拦截分支在任何 `_try_log` 之前返回），REST（`routes_search.py:22-25`）与 MCP（`mcp.py:204-206`）的拦截轮却照落事件且 `query_text`/`query_norm_hash` 带原话——拦截轮从未进入检索、无 eval/弱标注价值（与零命中轮的 `zero_result` 观测价值不同），叠加宿主对拦截事件从无回执，原话以 pending 形态滞留到 missed 抹文（票 10 机制）才有界。现：`log_retrieval` 增 `persist_query` 形参（缺省 True 行为不变），REST/MCP 拦截分支传 `persist_query=False`——**保留落事件**（拦截轮的 zero_result 遥测价值进 digest/recall 计数）但**写入时即空原话两列**；`is_system_noise`/`estimated_tokens` 等标记以原查询在抹除前照算（调用方直接传空串会让这两个标记静默失真的实现陷阱有回归锁）；`force` 旁路行为不变。ADR-0084 §2.5「边界不变」句按三通道实际行为改准（shell_hook 不落 / REST+MCP 落但脱文）。回归锁：`tests/test_gate_blocked_round_logging.py` 四例——真内存库直调：REST 拦截轮（真 gate、真 TestClient）断言行在 + `receipt_status=="pending"` + `query_text==""`/`query_norm_hash==""` + `zero_result is True`；标记先算后抹单元（>500 字符噪音查询 → `is_system_noise is True` + `estimated_tokens>0` + 原话两列空）；MCP `handle_search` 同口径（真 gate，不 patch relevance_check）；shell_hook 对照臂（拦截轮仍不落事件，行为不变）。

- **插件通道放行零命中 event_id 帧，serve 模式否决链补通最后一层（v0.68 票 74，巡检 2026-11-07 0700）**：v0.68 票 01 让 shell_hook 零命中分支带出 `event_id`（对齐 REST/MCP），但无常驻画像部署上零命中响应是 `{"event_id": ...}`（无 `context`），插件 `_call_hook` 的 `data.get("context")` 过滤把它整体丢弃——`_on_pre_llm_call` 挂载点到不了，被 DLP 否决的零命中轮构造不出 `retrieval_veto` 帧，ADR-0084 的 vetoed 终态/清 used_ids 语义对该轮不生效（票 10 的 missed 抹文只是 300s+ 的留存缓解）。本机实测仓库根/用户目录均无 `user_profile.md`，即维护者当前部署形态命中。现：`_call_hook` 放行条件放宽为「有 context 或有 event_id」（空帧/无元数据帧维持 None）；`_on_pre_llm_call` 对无 context 帧不产出空注入（`{"context": ""}` 会在宿主侧留空上下文条目）、底本在场时不再拼出 `\n\n` 开头空段，event_id 照常挂载，否决链经 `_on_session_end` 照常发帧。回归锁：`tests/test_plugin_zero_hit_eventid_passthrough.py` 五例——真子进程冒烟两例（helper 进程真实应答 `{"event_id"}` 帧 → `_call_hook` 放行；空帧维持 None）+ 插件链三例（零命中 event_id 挂载→被否决轮真发 veto 帧不回执/未否决轮不回执不 veto 照常写对话/空注入守卫）。

- **勘合 `release()` 无令牌不再抹掉 done 回执 + `purge_expired` 过滤下推 SQL（v0.68 票 75，巡检 2026-11-07 0700）**：`release()` 的 `state != "done"` 守卫此前只在带 `claimed_at` 分支内生效，无令牌调用（运维面强释放形态）把同键**全部**行连 done 回执一并删净——同键同文重投从 `replay` 退化为 `new`（重新执行整条摄取 = 重复建候选，正是勘合立项要防的原病；探针 `.scratch/_v068_probe_r9_release_nukes_done.py` 实锤：release 后重投 `claim` 返回 `new`，带令牌对照臂保住 done）。现守卫无条件生效：done 回执在任何 release 形态下都保留。同票顺带：`purge_expired()` 此前每小时全量加载「超租约 600s」的全部行（含 30 天 TTL 内的 done 回执）进内存再 Python 过滤，行数随时面线性增长；现 `(state != 'done' OR age >= TTL)` 与超龄条件一并下推 SQL，语义不变（done 未超 TTL 的行不再出库）。生产 `release()` 零调用方（纯运维面预留），接线即爆级——修在接线前。回归锁：`tests/test_idempotency_receipt.py` 增 3 例（`test_release_without_token_spares_done`——红→绿主锁含 replay 后果臂；`test_release_without_token_clears_accepted`——强释放语义保留锁；`test_purge_keeps_done_within_ttl`——下推 SQL 的「done 但年轻」态锁），28 passed 全绿。

- **非 veto 终态的检索原话不再永留库：missed 即抹文 + 扫描进调度器 + 存量 scrub（v0.68 票 10，ADR-0084 §2.6）**：ADR-0084 的立法意图是检索原话（含对低熵原文即可枚举验证的带索引 `query_norm_hash`）不应无代价永留库，vetoed 终态已抹，但非 veto 路径从未处理——`mark_missed_receipts` 置位只改状态不抹文，且生产零调用（唯一调用方是手动 CLI），未回执事件永停 pending、原话永留。现：① `mark_missed_receipts` 在 pending→missed 转移的同一事务里抹 `query_text`/`query_norm_hash`（与 `mark_vetoed` 同源论证；不清 `used_ids`、不回冲保活链——missed→acked 仍合法）；② 扫描挂进调度器（新 job `receipt_miss`，`RECEIPT_MISS_CRON_MINUTES` 默认 10 分钟、取值 5–60；monitor 配置清单与受控制台 `worker_schedule_specs` 同步登记）；③ 存量一次性脚本 `scripts/scrub_retrieval_query_text.py`（缺省 dry-run 只统计、`--apply` 写库、幂等，只处理 pending/missed，acked/vetoed 零接触）；④ ADR-0084 增补 §2.6 立法（补 §2.1 只谈「少一次弱标注」、从未谈原文留存的真空带）；⑤ 配套如实代价：`build_query_set` 样本池排除空 query（抹文后空行不掺 `sample_count` 口径）、recall 报告空 query 显示「(已抹除)」占位。回归锁：`tests/test_receipt_miss_scrub.py` 五条（真内存库直调：抹文留 used_ids / missed→acked 保活链不断 / 调度注册+真跑一轮 / scrub 幂等与零接触 / vetoed 结构性隔离——后两条为现状即绿的语义不变量锁）。
- **P2 文档清仓 19 项 + 发布门禁新增「文档引用对账」防复发哨兵（v0.68 票 09）**：流 C 审计 P2×20 搭车清仓（19 项 agent 修、1 项 ready-for-human）——README ADR 计数改「持续增补」防再过期；plugin.yaml 版本随行为变更提升 1.2.0（品牌/作者已随票 06 收口）；ADR-0042/0043/0046 补状态行（均已成落地事实，对照 0044 格式）；CHANGELOG 回归锁例数对齐现值 11；CONTEXT.md 补格物功能条目与 ADR-0084 新协议状态（retrieval_veto/mark_vetoed/receipt_status）技术字段登记（先登记再使用纪律补账）、fastpath 路径补前缀；future-roadmap 加「已全部落地」归档横幅、WHITEPAPER 加历史快照横幅（镜像名旧值按原貌保留）；key-rotation-runbook 旧机路径改占位符；mcp-client-matrix 数据目录回落改 settings.py 真实四段序（LANTAI_HOME→REMEMBRANCE_HOME→~/AppData/Local/remembrance-data→仓库默认）；手册工具前缀加「server key 与 serverInfo.name 两层命名」注；host-hook-protocol query 回落条件删「且带查询字段」（host_protocol.py 实证无前置）；daily-digest 统计表补第七项反思提案；development-workflow 清 .venv-verify/PYTHONPATH=. 过时口径（pyproject pythonpath 已配）并修示例裸路径；agents 文档示例树 src/→lantai/、triage-labels 加启用注记；pre-commit ruff rev 对齐项目钉值 0.16.6；CONTRIBUTING/glossary 测试硬计数改防过期措辞；DESIGN「零外部庞大框架依赖」收窄为「无构建步骤」。**防复发哨兵**：`release_check.py` 新增「文档引用对账」门禁项（第 3 位，共 13 项）——现役文档（docs/*.md 顶层 + README + CONTRIBUTING）引用的 `scripts/<name>.py` 必须真实存在，断线即点名 file:lineno 判红（流 C 审计 P0-3/6/7 同根因的结构性拦截）；豁免走显式白名单（现仅收编 P0-3 修复的「旧入口已删除」历史注记），新历史文档先登记再入档 fail-closed。回归锁：`tests/test_release_check.py` 判据级四样（红样点名死链/绿样真仓全过/白名单样/占位符样）+ `tests/test_docs_p2_sweep.py` 18 例（settings 现值、ADR-0013 双向登记、门禁 13 项连锁）。
- **使用面/流程文档 P1 十一组收口，门禁清单绑死唯一事实源（v0.68 票 08）**：P0「照做必败」之下的表述/口径/参数过时层一轮收口——host-hook-protocol 的空命中/超短/异常响应改写为 D-04 画像兜底帧口径（与 v0.68 票 01 零命中带 `event_id` 融合：零命中落库→画像帧+id，过短/异常→画像帧无 id，未配置画像才退 `{}`）、query 通道超时改 `_query_fallback` 降级画像帧（不再是「超时只返回空帧」）；插件文档回执段对齐 ADR-0084（延后到 `on_session_end` + 被否决轮发 `retrieval_veto`，不再写「注入成功后立即回执」）；used-ids 指南改 REST `POST /retrieval/backfill` 为主力通道并写明 MCP backfill 需 `LANTAI_MCP_PROFILE=full`（默认档 -32602），验证代码旧包名 `from remembrance.*` 改 `lantai.*`；release-process 镜像名对齐 CI 产物 `ghcr.io/<owner>/lantai`（照旧文档 pull 必失败）；**上传前门禁清单以 `release_check.py` 实际检查序列为唯一事实源重排为 12 项**（补 CI lint 门禁/CI 步骤覆盖核对/目标版本格式/构建产物 wheel/遗忘质量离线门禁五项，融合票 04 命名空间 provenance 项，pytest 自查口径对齐门禁真命令 `-o addopts=` + 覆盖率插桩）；ADR-0013 §7 版本代号表补登 v0.65.0 钩玄 / v0.66.0 探赜（双向登记闭环）；CONTRIBUTING 闸门示例换真实存在的 `tests/test_features.py`/`test_prefilter.py`（`test_gate.py` 不存在，照跑必败）；手册 gate 规则改 ADR-0028 放宽后六通道口径（技术词 ≥4 字/问句 ≥5 汉字放行、「帮我查」不在触发正则如实注明、`force` 参数登记、短追问沿用写明单向仅命中时），runbook 观察期参数对齐 `settings` 现值（14 天窗口/至少 7 次运行，旧「7 天中至少 3 天」清除）、`CANDIDATE_MIN_CONFIDENCE` 对齐 0.15（旧「当前 0.0」清除）。回归锁 `tests/test_usage_docs_p1_closeout.py` 14 例：门禁清单与 `release_check.py` 实际 check 序列逐项对表、pytest 口径与 `suite_pytest_command()` 判别片段比对、镜像名与 `ci.yml` 互证、ADR-0013 新行与 `CONTEXT.md` 双向登记互证、CONTRIBUTING 引用的测试文件存在性、gate 推荐说法与 no_signal 反例真实直调（先清 15s 热缓存防串味）、观察期/置信度参数与 `settings` 现值相等。
- **使用面文档「照做必败」七组一轮收口（v0.68 票 06）**：v0.38→v0.60 的四波变更（包更名、MCP 迁移+默认档收窄、画像兜底、否决收口）只回写了协议/CHANGELOG/ADR 层，README/矩阵/手册/runbook/插件文档这层照做必败——README 的 as_of 时态查询写在 URL query 上（FastAPI 静默忽略，调用方以为在查历史快照实则返回当前视图）、MCP 工具口径钉死「三工具」、`mcp-client-matrix` 钉死「40 工具」且默认档收窄零记录、手册默认端口 8000（实际 8767）且 add 示例缺 title/正文不足 10 字、runbook 的 `run_digest.py`（不存在）与 `docker compose up -d`（仓库无 compose 文件）、插件文档与 plugin.yaml 谎报「闲聊入待审队列」（实际沙汰为 rejected，ADR-0026，照文档验证必误判部署失败）、release 版本步骤指向不存在/无版本的文件。现全部对齐当前代码事实：as_of 族改请求体字段并保留「写在 URL 上会被静默忽略」警示；工具面统一「默认核心 6（fail-closed）/ `LANTAI_MCP_PROFILE=full` 全量 96」两档口径；手册端口/示例/速查表与 `settings.PORT`、`AddMemoryReq` 校验逐字对齐（no_signal 反例同步换为真实被 gate 拒的例句）；runbook 改指 `POST /monitor/workers/digest/run` / `GET /digest/today` 与 README「方式二：Docker 容器运行」；plugin.yaml 闲聊口径与品牌（Remembrance→Lantai）同步；版本步骤统一列 `lantai/api/app.py` / `lantai/cli/mcp.py` / `uv.lock`（与门禁 `VERSION_REF_SPECS` 一一对应），并收口门禁项标题与指路串的成对失配（「MCP 默认最小暴露」→「MCP 默认暴露」）。回归锁：`tests/test_usage_docs_p0_closeout.py` 19 例（文档示例直过真实 pydantic 校验、README 工具名与 `HERMES_CORE_TOOLS` 集合相等、版本锚 ⊆ 门禁真锚点、no_signal 反例真实 gate 实测、引用脚本存在性、「已删除」反指注记不算活引用）。
- **宿主 PYTHONPATH 遮蔽两向量闭合：tests 正包化 + 命名空间 provenance 发布门禁（v0.68 票 04）**：Hermes 宿主拉起的 shell 带全局 `PYTHONPATH`（hermes-agent 目录 + 宿主 venv site-packages）时两个位次同时遮蔽——其 `tests` 包压过本仓 `tests` 命名空间（全量收集即死）、三方依赖（pydantic）从宿主 venv 抢先解析（二进制不兼容直接崩）。现：① `tests/__init__.py` + `tests/support/__init__.py` 正包标记；② `tests/support/harness_state.py::scrub_foreign_sys_path` 装配期登记式清污（AST 扫描器对登记写入器豁免 SYSPATH_WRITE，其余文件仍一律判红）；③ `scripts/release_check.py` 新增「命名空间 provenance」门禁项——第一方（lantai/tests）与三方哨兵（pydantic/sqlmodel/fastapi）在当前门禁会话的解析落点必须全在本仓或活动解释器内，判红处置=清 `PYTHONPATH` 复跑，不改代码迁就环境；④ 口径登记进 `docs/release-process.md` 门禁清单。回归锁：`tests/test_pytest_collection_env_shading.py` ×3（收集/解析双臂 + 依赖遮蔽臂）+ `tests/test_release_check.py` provenance 三臂（含 sitecustomize 位次实证与修复前世界 tripwire）；真实 hermes PYTHONPATH 实境验证：全量收集 0 例即死 → 3094 例恢复。
- **拾遗零召回补救扫描补 lifecycle 过滤——superseded 旧说不经 `/shiyi/remedy` 直连面回灌（v0.68 票 15）**：`_remedy_recall` 的基础 stmt 不看 `lifecycle_status`，被更正闸换下的旧说（superseded）在零召回补救扫描里照捞回灌——票 02 给 context 建的排除口径在此面缺位（探针实锤：补救结果含两行 superseded）。现：基础 stmt 增补 `MemoryItem.lifecycle_status != LifecycleStatus.SUPERSEDED`（单条件过滤、异常行一并退出、DDL NOT NULL DEFAULT 无需 NULL 防御，与 context 口径逐字对齐；与注入面 guard「只认显式链」的口径分歧刻意保留，docstring 注明）；domain 收窄/verbatim 排除/预算熔断一行未动。回归锁：`tests/test_shiyi_supersede_filter.py` 4 例（主臂/异常行臂红→绿 + 对照臂/verbatim 现状锁），拾遗全家族 27P + `test_context_supersede_guard` 回归绿。
- **REST `/search` 面套更正闸 + `graph_expand` primary_results 漏旧值顺手覆盖（v0.68 票 13）**：更正闸消费方普查的第三条检索入口——`/search` 无 `_apply_supersede_guard`，同一更正链同一问句 MCP 已丢旧值、REST 新旧并列裸返（立案探针实锤）。现：闸挂 `filter_results_by_lanes` 之后、`_try_log`/`build_evidence` 之前（与票 02 mcp 半步同构：被丢旧值不进检索事件/回执/evidence；`as_of`/`as_of_recorded`/`time_from`/`time_to` 任一在场整段跳过；开关沿用 `SHELL_HOOK_SUPERSEDED_GUARD` 不新增）。**病灶 5 实测翻案**：立案探针读错 `graph_augmented_search` 返回键（`primary_results`）误判未复现，本轮定点探针实锤同形漏旧值——`graph_retriever` 在种子提取之后对 primary_results 挂同一闸（扩召行为逐字不变，闸只管输出面）。回归锁：`tests/test_rest_search_supersede_guard.py` 7 例（真内存库 + 真 `apply_supersede_correction` 直调路由；主臂/回看臂/只旧值臂 + as_of/开关现状锁 + graph_expand 新臂），更正闸全家族 60P 零断言改动。
- **MCP `add` 的 media_url 假承诺接线——schema 广告了、handler 静默丢弃（v0.68 票 07）**：`TOOLS["add"]` inputSchema 广告 `media_url`，`handle_add` 未消费——传了即静默丢，content 空被模型层校验挡下报「content required」，照 `mcp-client-matrix` 文档调用必败。现：`handle_add` 一行接线透传进 `AddMemoryReq`（走与 REST `POST /add` 同一条真目识链，`media_url 与 content 二选一` 等校验模型层天然同语义），文档「add 支持 media_url 图片感知」随接线自动转真。回归锁：`tests/test_mcp_add_media_url.py` 4 例——主臂（真目识链落库：caption 作正文、provenance 带 `vision-caption`）+ 对照臂（不传 media_url 行为逐字不变）+ 降级臂（vision 外部失败零落库，镜像 REST `test_add_vision_failure_no_write`）+ 哨兵（广告字段 ⊆ handler 消费面，防再假承诺）；`tests/test_vision.py` 10 例 REST 侧零回归。
- **MCP 入口三处断线收口，verify_lantai 自检恢复分辨力（v0.68 票 05）**：`scripts/` 下的旧平铺入口模块删除后，三处旧入口照跑必炸——`launcher_mcp.py` 的 `from mcp_server import main`（ModuleNotFoundError）、`launch_mcp.bat` 的同款 import 加旧机器绝对路径硬编码（`C:\Users\Asus\...` 与已不存在的 `.venv-audit` venv）、`scripts/verify_lantai.py` 的 `import scripts.mcp_server` 恒 FAIL 自检项。现三处统一唯一入口口径 `lantai.cli.mcp:main`（pyproject console script `lantai-mcp` 同源，不留第二种写法）：launcher 仓库根改 `__file__` 自定位、保留 Hermes PYTHONPATH 清污与 `.env` 加载职责、解释器非项目 venv 时如实死于可解释的 import 错误（不再 graft 外来 venv site-packages）；bat 改 `%~dp0` 相对定位 + `python -m lantai.cli.mcp`；verify 的 MCP 检查项改核 `lantai.cli.mcp:main` 可调用。顺手收口 verify 两处恒 FAIL 病（不修则「全项真环境 PASS」验收不可达）：Shell Hook 契约检查子进程补 `encoding="utf-8"`（Windows 下父进程缺省按 GBK 编码 stdin、hook 端 UTF-8 读，中文问句把 hook 打崩在解析层，HEAD 实测复现 `[FAIL] Shell Hook 契约 — 'NoneType' object has no attribute 'strip'`）；「数据目录已设置」未设 `LANTAI_HOME` 时改信息项 PASS（回落仓库默认目录本就是脚本支持的形态，实际路径由下一项 SQLite 存在性检查兜底校验）。回归锁：`tests/test_mcp_entry_points.py`（子进程干跑 launcher 不得现 ModuleNotFoundError、verify 实跑 MCP 检查项 PASS、进程内真实导入 `main` 可调用、三文件旧入口/旧机器路径零残留）。真环境实测 verify 8/8 全 PASS。
- **更正闸下沉 `lantai/retrieval/supersede.py`，context/MCP search 两消费面补闸（v0.68 票 02）**：更正闸（supersede guard）此前是 shell_hook 层私有函数，消费方普查只有 shell_hook/MCP/context 三面且只有前者已挂——同一更正链换下的旧说在 `/cognitive/context` 产物与 MCP search 结果里新旧并列裸返、不带「已更正」标记，异常行（superseded 无 superseded_by）照出（探针实锤）。现三笔收口：① 闸下沉为公开模块 `lantai/retrieval/supersede.py`（shell_hook 保留同名别名消费下沉版，既有 21 例回归锁零断言改动）；② `CognitiveContextBuilder.build()` 排除 `lifecycle_status=superseded` 的旧说（异常行一并退出）；③ MCP `handle_search` 套闸（时效视图 as_of 与开关语义不变）。回归锁：`tests/test_context_supersede_guard.py` + `tests/test_mcp_search_supersede_guard.py`（真内存库 + 真 `apply_supersede_correction`，不 mock 被测链路），全量 3063 passed。
- **fastpath 直写路径缺 `content_hash` 在场检查，重复内容撞唯一约束 500（v0.68 性能基线顺带发现，票 `.scratch/v068-perf/02`）**：提取路径 `_create_candidate_with_extraction` 建 `RawDocument` 前先按 `content_hash` 查在场（在场复用文档、照建候选），直写路径 `_create_candidate_direct` 没有这一步直接 INSERT——同一句自我声明（fastpath 白名单句式）在两次 coalesce 冲刷里各出现一次、且第一次尚未演化成 MemoryItem 时，前置的 `_apply_dedup` 只搜 MemoryItem 搜不到候选/文档阶段的重复，第二次冲刷即 `UNIQUE constraint failed: rawdocument.content_hash` → API 500。现直写路径补齐同口径在场检查（在场复用文档、照建候选，返回结构不变）。回归锁：`tests/test_fastpath_dup_content_hash.py`（真库 + 真实 ChromaDB 不 mock 直调 `add_memory` 两次：复现例 + 提取路径参照组钉住「行为一致」的口径）。
- **零命中轮的否决链补通：DLP 拦下的原话在零命中/空 evidence 轮不再永久留库（v0.68 票 01，ADR-0084 §2.5）**：v0.67 票 01 的否决收口只覆盖「检索有命中且 evidence 非空」的轮次——零命中分支把带原话的事件落库后经 `_persona_only()` 返回，`event_id` 被丢（三条检索入口里唯 shell_hook 漏，REST/MCP 零命中分支本就带）；插件挂载条件 `if event_id and used_ids` 又把挂载与 `used_ids` 耦合。两端合起来，DLP 否决零命中轮时插件构造不出 `retrieval_veto` 帧，`mark_vetoed` 永远够不着（探针实锤：响应 `{}`、库内事件 `query_text`==原话、`query_norm_hash` 非空）。现：`build_context` 零命中分支把已落库事件的 `event_id` 带出（`request_id` 该分支本就没生成，如实不带）；插件挂载只看 `event_id`（`used_ids` 有则一并挂），回执条件不变（零 `used_ids` 无可回执）、否决分支不变；`mark_vetoed` 零改动（对零命中事件本就正确）。回归锁：`tests/test_retrieval_veto.py::TestZeroHitVetoReachable`（空库真分发：响应带 event_id → veto 帧抹原话置终态；命中路径对照臂逐字不变）、`tests/test_plugin_receipt_defer.py::TestZeroHitVetoReachable`（空 evidence 被否决轮发 veto 不回执）。三处既有测试按新契约改口径（旧锁「零命中==空响应」）：`test_build_context_no_hits_no_evidence`、`test_main_timeout_returns_empty_json`（顺带补真超时前提——conftest 假 embed 瞬回让 0.2s 墙从未触发）、`test_empty_persona_short_query_returns_empty`。
- **被否决的一轮，检索日志不再留原话、注入回执不再照发（周报 2026-11-03 P0-1 第 4 点，ADR-0084）**：PR #26 只收口了对话写通道与底本五段块，检索侧三处假痕迹仍在——`log_retrieval` 把 DLP 拦下的原话存进 `RetrievalEvent.query_text`（+ `query_norm_hash`，归一化哈希对低熵秘密就是可验证口令）；插件在 `pre_llm_call` 检索成功后立即回执，此刻 Hermes 尚未裁决否决；回执链刷过 `injection_count`/`last_used_at`（从未发给模型的一轮多了假注入计数）。现：插件回执**延后到 `on_session_end`**（走到 flush = 本轮确认未被否决才发；被否决的条目改发新协议动作 `retrieval_veto`；无 session 的轮次保持旧行为立即回执——那些轮次的否决链本就不可达）；服务端新增 `retrieval_log.mark_vetoed(event_id)`：抹 `query_text`/`query_norm_hash`、清 `used_ids`、置 `receipt_status="vetoed"`（新终态，`backfill` 不复活、`missed` 扫描不碰、可追溯报告单列计数）；不做 `injection_count` 反冲（并发不安全，延后回执下假计数从根上不产生）。崩溃窗口回执如实 `pending→missed`（宁 miss 不脏写）。回归锁：`tests/test_retrieval_veto.py`（真库冒烟 10 例）、`tests/test_plugin_receipt_defer.py`（11 例）、`tests/test_host_protocol.py`（解析 6 例）。
- **默认 MCP 档补回 `offload_read`，注入帧不再叫模型调一个不存在的工具（巡检 2026-11-06 22:00）**：票 05 续31（`ff1dd12d`）把默认档收窄到 `HERMES_CORE_TOOLS` 5 个工具，但 `scripts/shell_hook.py` 给超长记忆的注入行仍写「…（已卸载全文；可调用 offload_read 工具查看）」（`_OFFLOAD_SUFFIX`，截断指南同理）。不设 `LANTAI_MCP_PROFILE` 时（`docs/hermes-install-handoff.md` 的配置就没设）`tools/list` 里没有它，`tools/call offload_read` 返回 -32602 unknown tool；`tests/test_offload.py` 被打了 `mcp_full_surface` 标记跑全量面，所以全量测试一直是绿的。现把只读、按 memory_id 反查归属的 `offload_read` 并入核心集（5 → 6），其余 90 个工具的收窄不变。回归锁：`tests/test_hook_named_tools_in_default_profile.py`（不打 `mcp_full_surface`，扫 shell_hook 里所有给模型看的文案，点名的工具必须都在默认档；并端到端从真实卸载注入取路径、默认档调回全文，以及默认档读别人的卸载全文仍被拒）。
- **Hermes 否决的一轮不再写进记忆（周报 2026-11-03 P0-1）**：Hermes v0.22.6 起插件可在 `pre_llm_call` 返回 `{"action": "block"}` 否决一轮（DLP 拦密码、密钥），这一轮不发给模型，`on_session_end` 的 `turn_exit_reason` 为 `blocked_by_plugin_pre_llm_call`。`lantai-hook` 在 `pre_llm_call` 开头就把原话放进会话缓冲，`on_session_end` 又不看退出原因，原话照样进了对话写通道（`ingest_dialogue`）和底本五段块。现在缓冲条目记下宿主给的 `turn_id`，否决时按 `turn_id` 剔除那一轮（旧版宿主不传 `turn_id` 时剔末条），之前正常的轮次照常落库。回归锁：`tests/test_plugin_veto_no_write.py`。

- **注入次数与显式反馈分账，考功/三省不再把天天在用的记忆降级（巡检 2026-11-03 P0，改法①）**：v0.66 的注入保活把宿主回执记进 `use_count`，而 Hermes 插件每轮注入都回执、从不发「有用」反馈——考功（`use_count>=3` 且采纳率 `<=0.2` → 重要度 0.1）与三省（`use_count>=5` 且 `helpful_count==0`）于是把最常用的记忆判成高频低效（实测重要度 0.95 的过敏记忆注入 3 次、跑一次考功即降到 0.1，160 天后被归档）。现在 `MemoryItem` 新增 `injection_count`，`retrieval_log._touch_keepalive` 只刷 `last_used_at` 并累加 `injection_count`；`use_count` 回到「显式反馈次数」原义，考功/三省/reflector 不动。遗忘侧 `forgetting._usage_signal` 把两者相加，衰减强度与 TTL 豁免与 v0.66 等价。迁移 v37 给老库补列，并按 `memoryusagefeedback` 账本把 v0.66 灌进 `use_count` 的注入次数挪回 `injection_count`（两者之和不变；无反馈表时只补列）。回归锁：`tests/test_kaogong_injection_count.py`。
- **模块级生产状态台账头部五条改为「有主纳管」（票 05 续23，G2 排期项）**：续22 的兜底护栏在全量结束时点名 `telemetry._writer` 366 例、`metrics._collector` 356 例、`prefilter._PATTERN_CACHE` 86 例、`_KEYWORD_WARNED` 83 例、`scheduler.WORKER_LAST_RUN` 68 例——都是真实生产代码被执行到时的正常副作用（懒单例首次使用、惰性编译缓存、`record_run()` 完成戳），却把台账撑到几百行。`tests/conftest.py` 新增三个显式依赖 `_isolate_module_state` 的主人 fixture（`_own_observability_singletons` / `_own_prefilter_keyword_cache` / `_own_worker_last_run`），**只释放能证明出自生产写入路径的状态**（自己预装的那个实例、`_self_reference_pattern()` 产物形状的缓存、ISO 时间戳形状的完成戳），外来实例/植入值/空串占位原样留给兜底护栏点名；护栏表 13 项与判红逻辑一行未动。`test_scheduler.sched_db` / `test_ingest_job_gone.ingest_db` 不再用 `setitem` 塞空串占位（撤销晚于护栏检查，每例被误点名）。生产代码零改动。回归锁：`tests/test_module_state_ownership.py`（不 mock 冒烟 + 越权拒绝 + 真实子会话含对照组）。
- **MCP 入口与 add 目识链的三处断线修复（文档一致性审计 P0-3/P0-8 代码侧，`.scratch/v068-docs-audit/` 票 01/02/03）**：`launcher_mcp.py` / `launch_mcp.bat` / `scripts/verify_lantai.py` 仍指向已删除的 `scripts/mcp_server.py`（照跑 ImportError，「8 项全 PASS」自检不可达），现统一改用现行入口 `lantai.cli.mcp`，启动器的仓库根与 venv 改为脚本位置相对解析（不再写死机器盘符；venv 候选 `.venv` 优先、旧名 `.venv-audit` 兜底）；MCP `add` 工具 schema 广告了 `media_url`（目识）但 `handle_add` 不读取——传了要么 -32602 要么被静默丢弃，现透传进 `AddMemoryReq`（非法组合由既有「media/content 二选一」校验拒绝，与 REST 口径一致），目识链在 MCP 面真实生效。回归锁：`tests/test_mcp.py::test_add_media_url_wired_to_vision`（不 mock 冒烟：直调 handle_add，caption 落 RawDocument 正文 + provenance 带 media_url）与 `::test_add_media_url_and_content_mutually_exclusive`（二选一拒绝 → -32602）。
- **使用面文档与代码一致性收口（审计 P0×7 组文档侧 + P1×11，`.scratch/v068-docs-audit/` 票 04-09）**：四波变更（更名、MCP 入口迁移+默认档收窄、画像兜底、否决收口）只回写了协议层，使用面文档（README / MCP 矩阵 / 手册 / runbook / 指南 / 插件文档）成组失真——本轮逐条对齐：闲聊去向改沙汰口径（ADR-0026）、版本同步步骤改列门禁真实核对面（`lantai/api/app.py` / `lantai/cli/mcp.py` / `uv.lock` 根包）、MCP 入口统一 `lantai.cli.mcp`、README as_of 时态查询改请求体字段（query 参数会被静默忽略）、工具面两档口径（默认核心 6 / full 96）、手册端口与 add 必填约束、runbook 不可用命令改真实入口、宿主协议画像兜底与回执延后语义、release-process 门禁 11 项真清单与镜像名、ADR-0013 §7 补登钩玄/探赜、gate 放行口径（ADR-0028）。审计报告：`docs/research/docs-code-consistency-audit-2026-11-07.md`。
- **手册与发布流程文档三处复核修正（审计复核新发现 R-1/R-2/R-3，`.scratch/v068-docs-audit/` 票 12）**：上一轮文档收口自身引入的失配——手册 no_signal 反例「大哥的电脑配置」实际双通道放行（`大哥` 命中自指正则、`配置` 命中技术词表，照文档理解会得出相反预期），换成真不命中任何通道的「物业费在哪交」（经真实 `relevance_check` 不 mock 冒烟证实：旧例 `self_reference` 放行、新例 `no_signal`）；「12 字内短追问沿用上一轮判定」注明单向（仅上一轮命中时沿用，`lantai/gate/prefilter.py:193-195`）；release-process 第 7 项标题对齐门禁输出名「MCP 默认暴露」（门禁失败文案指路串随行同步），第 3 项「目标版本格式」注明仅失败路径记账（绿灯汇总为其余 10 项 PASS）。

### Security

- **`/import/jsonl` 批量导入挂獬豸与密钥闸——守卫矩阵唯一漏网写入面闭合（v0.68 票 47）**：`lantai/services/import_service.py::import_memory_lines` 此前 sha 去重 → verbatim 直存 → FTS+向量全程零闸——持 API key 的调用方逐行导入 JSONL（聊天导出、网页摘录、第三方笔记），注入载荷与密钥（sk-…/AKIA…/ghp_…）直入 FTS+向量，检索时经 `retrieval/evidence.py` `content[:200]` 进宿主 additionalContext，獬豸「注入内容进不了库」边界对本面失效；守卫矩阵全仓 11 处挂闸面（memory_service 四路/persona/probing/record_ops/routes_terminal/dialogue/ingest_worker/qingniao/shishi/tanyi）独漏本面。现逐行双闸（单文件，零票外改动）：① `assert_no_prompt_injection` 命中记 errors 不导入（宁 miss 不脏写）；② `detect_secret_shapes` 三态照抄 ingest_worker 口径——off 不检测 / shadow 检测+warning 留痕不阻断 / enforce 命中拒导入（本面 verbatim 直存无候选可转待审，`route_secret_hits` 需要 candidate_id，errors 报告即「不静默」载体）。**闸位在 sha 去重之前**——duplicate 命中不等于过闸，毒行不得借重导洗白（红测专臂锁定）。对账：`lantai/ingestion/import_service.py`（会话 JSONL 导入）逐行喂 `ingest_dialogue` 已挂双闸、MCP 无 import 面——唯一漏网即本面。熵增登记：写前闸挂法至此第 5 处复制（票面 Answer 登记待收敛 helper，后续轮收口）。回归锁：`tests/test_import_gates.py` 5 例（红 **4F/1P**：主臂 imported:3 毒行直入、重导臂 duplicates:1 去重洗白、shadow 臂无留痕、哨兵臂源码零闸调用；off/shadow 不阻断现状锁如实申报）+ 守卫家族 116P。
- **RSS/arxiv 外部摄取挂獬豸闸与密钥检测——守卫矩阵唯一无闸候选写入面闭合（v0.68 票 03）**：ingest 面是全仓唯一无獬豸闸的候选写入入口，注入载荷与密钥经 RSS/arxiv feed 直入 FTS+向量（gate 矩阵全仓普查 11 挂载点 vs import 面零闸）。现（`lantai/workers/ingest_worker.py` 单文件，零票外改动）：① 逐 doc 在 `extract_candidate` 之前对 `title+content` 挂 `assert_no_prompt_injection`——命中跳过候选创建、RawDocument meta 整属性重赋记 `xiezhi_blocked` 留痕（避开 JSON 列原地改不被 dirty 跟踪的审计陷阱）、逐 doc 捕获 ValueError 不拖停整批；② `detect_secret_shapes` 三态路由（off 直通 / shadow 检测记录不阻断 / enforce 命中转待审）在**批次提交后**执行（事务内读不到未提交行会被 StaticPool 共享连接遮成假绿）。回归锁：`tests/test_ingest_xiezhi_gate.py` 7 例（4 红 + 3 现状锁；候选按 `document_id` 反查防降级 summary 假绿、meta/终态新开 session 重读、C 臂钉首 job `done` 挡整批回滚偷懒实现）。

## [0.66.0] - 2026-11-02 - 探赜（Tanze · 续审 P0 收口与发布门禁去橡皮图章）

> 版本代号「探赜」：探赜索隐、钩深致远，语出《周易·系辞上》——对应本版主题「v0.38–v0.60 续审 P0 清单收口 + 发布门禁可信度」：已删隐私不再重新注入（X-01）、豁免/重要记忆不再被 60 天零反馈误归档（X-02）、私密记忆数据停止入库（X-05）、测试隔离与全局态污染护栏（票 01）、探颐中文否定语义误归档（票 02）、插件短查询画像注入（票 04）、门禁新增四类结构性阻断（票 05）。票据 `.scratch/v0.66-next-window-2026-11/`（票 01–04 resolved；票 05：默认 MCP 暴露已由续31 收窄为默认最小面，G3 阻断解除，见 Security 段）。

### Security

- **拾遗兜底击穿检索契约闭合（N-01 后半句，票 05 续32）**：`hybrid_search` 三个零召回兜底站点此前只传 `query/domain/limit`，主流水线的 verbatim 默认不召回、`as_of` 时效视图、`lanes` 隔离三条契约全被兜底面击穿（实测：主路 `[]` vs 兜底返回 verbatim 逐字原文行）。现：契约参数（memory_types/lanes/shared_scope/as_of/time_from/time_to/as_of_recorded）在场、或匿名 `principal=None` ⇒ 一律跳过兜底（宁 miss 不脏召）；verbatim 排除同时做进引擎扫描侧——`/shiyi/remedy` 直连入口绕过水线，契约必须在两个入口同开关同方向成立。租户/属主面实测不泄漏（`_check_access` 在岗），本轮未动。锁：`tests/test_shiyi_remedy_contract.py`（5 判据，前提/断言/对照三段式防假绿；分层变异 X1/X2 各咬各层）。
- **MCP 默认最小暴露（G3 收窄，票 05 续31）**：`lantai/cli/mcp.py::_get_active_tools()` 此前不设 `LANTAI_MCP_PROFILE` 即返回全量注册表（96/96），且 `safe`/`none`/拼错的取值**静默保持全量**（评审线 N-04/C-15/R-02 记录的 fail-open 默认档）；`tools/call` 的分发面与 `tools/list` 同源，意味着默认档下 96 个工具全部可被真实执行。现改为：默认/空/未知取值一律 fail-closed 到 `HERMES_CORE_TOOLS` 核心集（5 个；未知取值额外 `UserWarning` 告警，不静默），全量注册表只有显式 `LANTAI_MCP_PROFILE=full` 才给。`tools/list` 与 `tools/call` 的 membership 判定继续同源（`handle()` 单点计算活跃集，收窄同时关广告面与执行面）。安全门禁：`tests/test_mcp_default_minimal_exposure.py`（6 判据：默认==核心集/未知 fail-closed+告警/显式 full 唯一放宽/广告面==活跃面/默认档外调用拒 -32602 且 handler 不执行/核心 5 不误杀）；门禁项「MCP 默认暴露」判据不变（结构性的，默认==全量即阻断），红灯文案换绑新语义并由双向对账哨兵钉住；口径登记于 `docs/release-process.md` 第 8 条。依赖非核心工具面的测试模块显式打标 `pytestmark = pytest.mark.mcp_full_surface()`（34 个文件），不改任何用例断言。
- **已删隐私不再重新注入 Hermes（X-01，票 05 P0-A）**：`memory` 是软删除而 `fact_atomic` 有意不级联，「删掉的隐私还能不能被读到」完全取决于每条读通道是否 JOIN 回来源判存活。补齐三处漏网——`lantai/retrieval/wanbi_retriever.py`（公开 `fact_source_alive_clause()`，完璧/一跳/二度共用）、`lantai/retrieval/graph_retriever.py`（贯珠两处 outerjoin）、`lantai/services/probing_service.py`（探颐事实查询 outerjoin + `_visible()` 状态闸）；注入侧 `scripts/shell_hook.py::_live_atomic_facts()` 在拼进 prompt 前 fail-closed 复核来源存活（新增 `tests/test_x01_privacy_reinjection.py` 13 项）。既有契约未动：`as_of` 视图、领域/agent 隔离、`verbatim` 默认不召回、软删不级联、硬删级联。
- **私密记忆数据停止进入公开仓库（X-05，票 05 P0-C）**：`docs/memory-offload/`、`docs/memory-wiki/` 自提交 `bbbc4496` 起撤出 git 跟踪并写入 `.gitignore`（`git ls-files` 为 0）。**未闭合**：历史提交里仍存在已入库内容，历史清除属维护者决策（R-5）。

### Fixed

- **重要/豁免记忆不再被误归档（X-02，票 03 P0-B）**：归档判据 `tier==working AND helpful_count==0 AND age>60d` 此前不看 `importance`。现由 `lantai/memory/forgetting.py::_decay_exempt` 读 `settings.FORGET_IMPORTANCE_EXEMPTION_FLOOR=0.8`（出厂行为逐字不变、阈值可配；衰减路 / TTL 路 / `consolidation_service.prune_decayed_synapses` 共用同一判据，三处一起生效）。使用保活走注入回执路径：`lantai/observability/retrieval_log.py` 在宿主 backfill 成功时回写 `use_count += 1` 与 `last_used_at`，带属主校验、失败非致命——仅经插件/检索注入使用的记忆不再恒被当成「零反馈」（新增 `tests/test_forgetting_exempt_archive.py`）。
- **测试隔离与生产全局态污染（票 01）**：`tests/conftest.py` 新增规范绑定登记表与 `_repair_canonical_bindings` 复位护栏，会话收尾打印「跨测试模块绑定残留」点名污染源；`tests/test_isolation_shared_state.py` 保留一对演示用例（3a 故意制造污染、3b 证明护栏能复位并指名来源）。基线 `ac30af91`（v0.60.0）全量 46–50 红中有 16 条纯属运行顺序泄漏受害者，与产品代码无关。
- **探颐中文答复误归档（票 02）**：`lantai/services/probing_service.py` 的否定/短语边界判定不再依赖裸 substring 包含（回归锁 `tests/test_probe_reply_cjk_semantics.py`）。
- **插件短查询丢常驻画像（票 04，D-02）**：`hermes-plugin/lantai-hook/__init__.py` 的 15 字硬编码预过滤不再把短问句/短追问挡在画像注入之外（回归锁 `tests/test_plugin_short_query_persona.py`）。
- **门禁 CHANGELOG 维度假绿闭合（票 05 续46）**：`scripts/release_check.py::check_version_consistency` 此前对 `changelog_text is None`（CHANGELOG.md 缺失/改名/路径错）整段静默跳过——版本一致性照样 `[PASS]`，PASS 文案还声称「CHANGELOG 最新段已核对」（红样实测逐字：`[PASS] 版本一致性 — 2 处版本引用 + CHANGELOG 最新段 = v0.66.0`，而文件不存在）。CHANGELOG.md 不在 `VERSION_REF_SPECS` 七处引用里，refs 侧兜不住。现按同脚本法典化口径（addopts「依据缺失 = 未取证，不放行」）改为：取不到文本即产出阻断项。回归锁：`tests/test_release_check.py`（判据级 + 消费级单变量实验正反两向，`check_version_consistency` 本体不被 stub）；修复前红样与绿灯留存 `.scratch/gate/baseline/r62-red-in-tree.log`（2 failed/3 passed，红样含逐字假绿行）/ `r63-green-after-fix.log`（53 passed EXIT=0）。
- **用例结果不再随宿主环境翻转（票 05）**：`tests/test_host_matrix.py`、`tests/test_hook_stdout_protocol.py`、`tests/test_install_host_hooks.py` 的子进程环境自带 `API_KEY`（hook 的密钥闸门要求非空且禁止 DEV MODE 回退——生产契约未动，只是用例如实声明前置）。实测无密钥环境由 8 failed 转 27 passed，全量在无 ambient 密钥下 2744 passed。`tests/test_longmemeval_core.py` 的数据集改为仓库根锚定，消除「这条评测线跑不跑取决于调用 cwd」（锁：`tests/test_eval_dataset_path_determinism.py`）。

### Added

- **发布门禁去橡皮图章（票 05）**：`scripts/release_check.py` 新增四类结构性阻断（判据一律结构性，不含可调阈值）——全量测试 + 宿主数据目录绊线（`suite_gate_issues`：红主干不得上传，超时不是绿灯）、默认 MCP 暴露 == 全量注册表即阻断（`mcp_exposure_issue`）、临时 `LANTAI_HOME` 内真跑 `init_db()` 并比对 metadata 与实表的 schema/NULL 约束漂移探针、绊线看护对象校验（`host_home_for_watch` 在父进程侧解析宿主库 + `tripwire_armed_path` 比对武装路径：武装在沙箱而非宿主库时，`[HIT]=0` 属于「没检测」，按红处理）。配套 `tests/support/real_home_tripwire.py`（按连接路径判定，不按 mtime——开发机常驻服务自己也在写库）与 `tests/test_release_check.py` 反例集（红项目必须拦、绿项目必须放、真实库被连必须拦、绊线没装载必须拦）。背景：v0.60.0 与 v0.65.0 两个 tag 都由 237/238 行、**一行测试都不跑**的旧门禁放行，而 `cba93612`（= tag v0.65.0）自身实测 22 failed。

### Known limitations

- 默认 MCP 暴露已收窄为 `HERMES_CORE_TOOLS` 核心集（Security 段续31 条目）；需要全量工具面的部署必须显式设 `LANTAI_MCP_PROFILE=full`——这是行为变更：此前不设变量的调用方若一直在用核心 5 个之外的工具，升级后会收到 unknown tool（-32602），需按部署侧口径显式声明。
- 仓库无类型检查步骤（无 mypy 配置、未安装）；精确工具数由单一魔数哨兵 `tests/test_mokan_service_and_mcp.py::test_seam3_mcp_96_tools_parity` 钉住。
- 老库（`user_version<N`）迁移后的列漂移不在 schema 探针覆盖内（D-13 另票）；`conflicts._temporal_recency` 的 30 天墙上时钟钳制本轮只加现状锁（R-3）。

## [0.65.0] - 2026-11-01 - 钩玄（Gouxuan · 存量积弊清仓与防注入强化）

> 版本代号「钩玄」：钩沉存量积弊，探赜索隐补遗，语出韩愈《进学解》「拔萃便适，钩玄提要」——对应本版主题「两份深度评审报告存量清仓」：常驻画像樊篱防注入与截断兜底（D-05）、网络调用预算传导与超时不丢画像（D-04）、RRF 与 Reranker 检索分路阈值（D-03）、SiliconFlow 跨服务商密钥泄露阻断（X-09）、README 动态徽章与私有路径彻底剥离（X-10, C-03）。票据 `.scratch/v0.65-next-window-2026-11/`（票 01–06 全部收口）。

### Security
- **阻断向第三方端点外发 OpenAI 密钥（X-09，票 04，`lantai/retrieval/reranker.py`）**：仅当端点域名同属 `api.openai.com` 时才允许回退复用 `OPENAI_API_KEY`；使用 SiliconFlow 或异构服务商且未配独立密钥时安全短路返回空列表，宁 miss 不外发（新增 `test_reranker_key_leak_guard.py`）。
- **常驻画像防提示注入樊篱与字数截断（D-05，票 02，`scripts/shell_hook.py`）**：画像三段（profile/persona/core）全部经过 `wrap_as_data` 樊篱包裹，前置 `fence_declaration`，并对 `user_profile.md` 施加 4000 码点硬上限防护（新增 `test_persona_injection_fence.py` 11 项全绿）。

### Fixed
- **检索超时不丢画像与预算传导（D-04，票 01，`scripts/shell_hook.py`）**：检索超时（2.0s 预算）兜底回退带围栏的常驻画像，杜绝超时连带丢画像；预算向下层网络调用逐级传导（新增 `test_hook_timeout_budget.py`）。
- **相似度分路阈值隔离量纲（D-03，票 03，`scripts/shell_hook.py` + `settings.py`）**：区分 Reranker（0.5 语义下限）与 RRF 融合分（0.15 独立量纲），新增 `SHELL_HOOK_SCORE_THRESHOLD_RRF` 配置项，解决降级时排第二候选被误杀（新增 `test_score_threshold_dimension.py`）。
- **动态 CI 徽章与私有绝对路径清除（X-10, C-03，票 05）**：README 徽章改为动态工作流状态，移除写死测试数；插件侧彻底剔除 `Path("D:/Projects/记忆")` 开发者机器绝对路径残留。

## [0.64.0] - 2026-10-31 - 缮治（Shanzhi · 协议面净化与 P0 存量收口）

> 版本代号「缮治」：修缮整治、补葺去垢，语出《汉书·货殖传》「缮治器物」、《后汉书》「缮治城郭」——对应本版主题「协议面净化与 P0 存量收口」：hook stdout 协议流净化（N-01）、E2 双视图评测归零（D-06/票 06-08）、探颐否定子串误归档修复（R-01）、考功写副作用身份闸（N-03）、G1 发版闸门（红灯不出镜像），v0.38–v0.60 续审报告 P0 行动清单 7 项全部闭合。票据 `.scratch/v0.64-next-window-2026-11/`（票 01–11 全部收口）。

### Security

- **G1 发版闸门：镜像构建前置测试（票 10，`.github/workflows/ci.yml`）**：tag push 先跑 tests job（ruff 双门禁 + 全量 pytest，口径同 tests.yml），全绿才构建推送镜像（`build.needs: tests`）——「红灯出镜像」路径关闭（v0.39–v0.59 共 21 个标签未经 CI 直接出镜像的隐患面就此闭合）。
- **考功写副作用身份闸（N-03 残留，票 09，`lantai/cognition/kaogong.py`）**：`run_cycle` 在 `principal is None`（MCP `user_id` 不透传）时空转返回 `skipped_no_principal`，不再降权写回 NULL 属主行（实测 0.5→0.1 可达，真实库 391/635 条 verbatim 全 NULL）——宁 miss 不脏写，写路径先验身份；系统身份与具名用户行为零变化（调用面普查：REST×2 必有身份、无 scheduler/worker 调用）。

### Fixed

- **hook stdout 协议流净化（N-01，票 01，`lantai/core/logger.py` + `scripts/shell_hook.py`）**：logger stdout→stderr 一行修复——chromadb posthog telemetry 与 WARNING 日志不再混入 NDJSON 帧流，宿主侧 `json.loads` 不再炸（三宿主 E2E 自 v0.62 以来首次全绿，票 07 连带闸门假阴性四层根因修复）。
- **探颐否定子串误归档（R-01，票 09，`lantai/services/probing_service.py`）**：`_NEGATIVE_PATTERNS` 英文否定词整词化（`\bno\b` 等与肯定侧 `_is_affirmative_reply` 对称）——`'noted, 已记录'` 曾被裸子串 `no` 误判否定直接归档记忆（置信度 0.1）；CJK 否定词语义不动，三分支同源生效。
- **E2 双视图评测归零（D-06 根治链，票 06–08）**：chronos 当前态语义污染 as-of 历史视图——SQLModel 0.0.42 `default_factory=utcnow` 吞显式 `valid_from=None`（票 08 根治：列默认 `default=None` + 12 服务层写入点显式 `valid_from=utcnow()` 落锚，I2 责任移交服务层对齐迁移 v21 回填口径）；当前态写入守卫一行修复（票 06），`e2_temporal_cases` 内 SQL NULL 补正 hack 撤除，accuracy 0.8378→**1.0**；`test_chronos_asof_open_interval` 同步归绿。

### Performance

- **拾遗扫描预算（票 02 延伸，`lantai/gate/prefilter.py` + `lantai/retrieval/hybrid.py`）**：闸门预筛与检索链路预算收口（详见 `.scratch/v0.64-next-window-2026-11/map.md` 票 01–07 行）。

### Tests

- 新增 5 个测试文件、17 项不 mock 冒烟（真实 SQLite 直调公开接口）：`test_hook_stdout_protocol`（协议帧净化）、`test_probe_reply_word_boundary`（R-01 整词边界 5 项）、`test_kaogong_no_principal_write_guard`（N-03 身份闸 3 项）、`test_valid_from_open_interval`（票 08 开端开放语义 6 项）、`test_chronos_asof_open_interval`（as-of None 保真）。
- 3 个锁定旧行为的既有测试按新契约更新（`test_kaogong` / `test_kaogong_engine_smoke` 补显式系统身份；`test_mcp_none_scope_leaks` 翻转 NULL 老行写回断言，docstring 记录票 06→09 口径演进）。
- 全量回归 run 9 = **17F / 2521P**（失败集与 run 7 基线 comm 逐项一致，17 项均为已知单跑绿乱序簇），vs v0.63 基线 27F 净修 10 项。

## [0.63.0] - 2026-10-31 - 拾阙（Shique · 稽失防微与闸门补阙）

> 版本代号「拾阙」：拾遗补阙，语出《史记·太史公自序》「拾遗补阙，成一家之言」——寓意拾补阙漏、防微杜渐，对应本版主题「稽失防微，固本培元；系统健壮性、闸门审计与自愈防线修复」：承接 v0.38–v0.60 续审报告揭示的 P1 核心风险，完成终端合并自删除防呆（X-03）、人格基座注入闸门（X-04）、密钥熔断全链路覆盖（X-06）与拾遗零召回全表扫描治理（N-02）。票据 `.scratch/v0.63-next-window-2026-11/`（票 01/02/03 全部收口）。

### Security

- **终端合并自删除防呆与合并面獬豸闸门（X-03，票 01，`lantai/api/routes_terminal.py`）**：`merge_memories` 入口首行拦截 `source_id == target_id`（400）——旧逻辑先自追加再 `session.delete(src)` 物理删除自身，数据静默丢失；合并产生的新正文（落 FTS+向量改写面）前置獬豸注入闸，命中 422（与 `correct_memory` 同族同闸）。
- **人格基座写入注入闸门防护（X-04，票 01，`lantai/services/persona_service.py`）**：`set_persona` 的 name/L/G/E 四字段前置獬豸——人格基座是每次 prompt 必注入面，service 层挂载一处覆盖 REST `POST /persona` 与 MCP `persona_set` 双写入面。
- **密钥熔断全链路覆盖与默认影子检测（X-06，票 02，`lantai/security/secret_guard.py` + `lantai/services/memory_service.py` + `lantai/ingestion/dialogue.py`）**：检测与路由逻辑下沉 `secret_guard.py` 单一真源（`detect_secret_shapes`/`route_secret_hits`）；`add_memory`/`add_memory_async` 全部候选创建点挂载（REST `POST /memories`、MCP `add`、coalesce 同步/异步冲刷面全覆盖）；`SECRET_GUARD_MODE` 默认 `off → shadow`（检测+记录+不阻断——enforce 会把正当密钥记忆强制挂起人工裁决，是假阳性放大器）。

### Performance

- **拾遗零召回全表扫描 SQL 预筛与预算熔断（N-02，票 02，`lantai/cognition/shiyi.py`）**：token `LIKE` SQL 预筛先行收窄（无关行不进 Python 逐条匹配，实测 21 行库 `scanned_count` 21→1）；新增 `SHIYI_SCAN_BUDGET_MS=800` 扫描预算熔断，超时即中断并标注 `scan_truncated`（partial 语义）；返回契约扩充 `scanned_count`/`scan_truncated`。

### Tests

- 新增 `tests/test_terminal_merge_gate.py`（6 项不 mock 真实测试：自合并 400 防呆 + 正常合并正面对照 + persona 注入闸门 service/REST 双面）；
- 新增 `tests/test_key_gate_and_shiyi_perf.py`（10 项：密钥闸默认模式/shadow 不阻断/enforce 转待审/双路径覆盖/干净内容零误伤/dialogue 回归锚定 + 拾遗预筛契约/熔断时钟注入/不超支不少标）；熔断测试全程时钟注入零 `time.sleep`，无测试泄漏。

## [0.62.1] - 2026-10-31

> 补丁版（免代号，沿 v0.23.1–v0.23.3 先例）：修复 v0.62.0 发布后暴露的完璧回归与测试快照陈旧。

### Fixed

- **完璧（Wanbi）X-01 事实查询与级联删除回归修复（4664ffcb）**：修复多实体事实原子保整链路中事实查询与级联删除在 X-01 安全评审中暴露的回归，恢复完璧全链路的正确行为。
- **MCP 工具池 parity 测试快照陈旧**：`test_zhaji_api_smoke` / `test_zhiming_api_smoke` 接缝 3 硬编码 93/94 工具数，与磨勘窗口 96 工具现状不符；改为动态下限断言（`>=`）+ 双向对齐守卫保留，工具数随版本扩容不再误红。

## [0.62.0] - 2026-10-30 - 磨勘（Mokan · 多源认知证据交叉勘验与可信度自证引擎）

> 版本代号「磨勘」：出自《宋史·选举志》「磨勘之法，核验资历功过以定升降」，宋代设审官院、三班院核验官吏实绩功过与文书证据——取「核验多源证据功过、自证记忆可信度」之意——对应本版主题「多源认知证据交叉勘验与可信度自证引擎，ADR-0083 落地」：将多源异构证据交叉核对、事实可信度自证打分与矛盾冲突勘验提升为一级认知引擎 `MokanEngine` 与 `MokanVerificationItem`/`MokanAuditResult` 领域契约（票 01）。支持纯函数可信度自证打分（`compute_trust_score`，多源边际递减增益与矛盾惩罚降权）；支持多源证据拓扑交叉核验与互证支持链追踪（`cross_verify_memories`）；支持存量事实全局矛盾冲突审计与争议状态标记（`audit_contradictions`）；全链路行级租户安全隔离与樊篱防注入。提供 `mokan_service.py` 服务层统一入口；挂载 HTTP REST 路由（`POST /mokan/verify` 与 `GET /mokan/audit`）；MCP 工具池扩充至 **96 工具里程碑**（新增 `mokan_cross_verify`），实现 `TOOLS` 与 `TOOL_HANDLERS` 1:1 双向一致性守卫；增强认知上下文 `CognitiveContext`，将磨勘切面深度集成至 `mokan_digest` 中（票 02）。全量覆盖端到端不 mock 冒烟测试。票据 `.scratch/v0.62-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **核心磨勘多源认知证据交叉勘验与可信度自证引擎 MokanEngine 与认知层收敛（票 01，详见 `lantai/cognition/mokan.py` 与 `tests/test_mokan_engine.py`）**：
  - 纯函数可信度自证打分算法（`compute_trust_score`）：证据数 > 1 时产生边际递减的交叉印证增益，检测到矛盾冲突时施加阶梯惩罚降权，严格受限在 [0.0, 1.0] 闭区间；
  - 多源异构事实交叉勘验（`cross_verify_memories`）：基于拓扑关系（supports/refines/contradicts）核验多源事实，自动标注 `verified`/`disputed`/`unverified` 状态；
  - 全局矛盾与争议审计（`audit_contradictions`）：支持关键字过滤或全库扫描未决冲突，输出结构化 `MokanAuditResult` 并生成防注入 `<mokan_audit>` 围栏块；
  - 行级租户属主安全隔离与樊篱防注入：严格校验 `_check_access`（宁漏不越权），全链路正文采用 `<memory_data>` 围栏包裹；
  - 交付端到端不 mock 冒烟测试 `tests/test_mokan_engine.py`（4 接缝：可信度算法边界、一致性多源印证、矛盾识别降权、行级多租户隔离全绿）。
- **磨勘服务层封装、REST 路由、MCP 96 工具池扩充与认知上下文切面（票 02，详见 `lantai/services/mokan_service.py`、`lantai/api/routes_mokan.py`、`lantai/cli/mcp.py` 与 `tests/test_mokan_service_and_mcp.py`）**：
  - 服务层封装：提供 `mokan_verify_service` 与 `mokan_audit_service` 统一入口；
  - HTTP REST 路由：新增 `POST /mokan/verify` 与 `GET /mokan/audit` 路由端点，挂载入 `CORE_ROUTERS`；
  - MCP 扩充至 96 工具里程碑：注册 `mokan_cross_verify` 工具规范（支持 `verify`/`audit` 双模式）并挂载处理器 `handle_mokan_cross_verify`，保持 `TOOLS` 与 `TOOL_HANDLERS` 1:1 双向对齐；
  - 认知上下文切面：`CognitiveContext` 新增 `mokan_digest` 属性，在 `to_prompt()` 中渲染结构化磨勘切面（含可信度分数与状态，正文严守樊篱包裹）；
  - 交付端到端不 mock 冒烟测试 `tests/test_mokan_service_and_mcp.py`（4 接缝：服务层调度、REST 端点往返、MCP 96 工具双向对齐、认知切面渲染全绿）。

## [0.61.0] - 2026-10-30 - 钩沉（Gouchen · 历史语境回溯与沉埋记忆检索恢复引擎）

> 版本代号「钩沉」：出自《易·系辞上》「探赜索隐，钩深致远」，宋代王应麟《困学纪闻》等考据治学常用「钩沉」指打捞湮没典籍——取「钩取沉埋之物、打捞沉没记忆」之意——对应本版主题「历史语境回溯与沉埋记忆检索恢复引擎，ADR-0082 落地」：突破既有常规 Hybrid 检索只看活跃浅水区、无法触达归档/弱化深层记忆、缺乏历史时态语境回溯能力的局限，构建面向生产环境的一级认知打捞引擎 `GouchenEngine` 与 `GouchenItem`/`GouchenResult`/`HistoricalContext` 领域契约（票 01）。支持沉没度打分（`SunkenScore = (1-decay) × age/(age+30)`）、穿透 `archived`/`weakened` 状态的深水区多维打捞（`dredge_sunken_memories`）；支持基于双时间轴锚点 $T_{anchor}$ 的历史语境无未来污染快照重建（`reconstruct_historical_context`）；全链路行级租户安全隔离与樊篱防注入。提供 `gouchen_service.py` 服务层统一入口；挂载 HTTP REST 路由（`POST /gouchen/dredge` 与 `GET /gouchen/snapshot`）；MCP 工具池扩充至 **95 工具里程碑**（新增 `gouchen_dredge`），实现 `TOOLS` 与 `TOOL_HANDLERS` 1:1 双向一致性守卫；增强认知上下文 `CognitiveContext`，将钩沉切面集成至 `gouchen_digest` 中（票 02）。全量覆盖端到端不 mock 冒烟测试。票据 `.scratch/v0.61-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **核心钩沉历史语境回溯与沉埋记忆打捞引擎 GouchenEngine 与认知层收敛（票 01，详见 `lantai/cognition/gouchen.py` 与 `tests/test_gouchen_engine_smoke.py`）**：
  - 沉没度打分纯函数引擎：基于 `(1-decay_score) × age/(age+30)` 计算记忆沉没深度，衰减越彻底、存续越久远，沉没度越高；
  - 深水区穿透打捞（`dredge_sunken_memories`）：突破常规检索状态隔离，显式穿透打捞 `archived`/`weakened`/低分沉埋条目，按沉没度降序输出 `GouchenResult`；
  - 历史语境快照无未来污染重建（`reconstruct_historical_context`）：基于时序锚点 $T_{anchor}$ 抓取当时生效记忆（`created_at ≤ anchor`, `valid_from ≤ anchor < valid_to`），绝对阻断未来信息泄漏，输出 `HistoricalContext`；
  - 行级租户属主 ACL 隔离与樊篱防御：严格校验 `_check_access`（宁漏不越权），全链路正文采用 `<memory_data>` 围栏隔离；
  - 交付端到端不 mock 冒烟测试 `tests/test_gouchen_engine_smoke.py`（4 接缝：纯函数打分、真实 SQLite 穿透打捞、历史快照无污染重建、行级 ACL 隔离与樊篱防注入全绿）。
- **钩沉服务层封装、REST 路由、MCP 95 工具池扩充与认知上下文切面（票 02，详见 `lantai/services/gouchen_service.py`、`lantai/api/routes_gouchen.py`、`lantai/cli/mcp.py` 与 `tests/test_gouchen_api_smoke.py`）**：
  - 服务层封装：提供 `gouchen_dredge_service` 与 `gouchen_snapshot_service` 统一入口；
  - HTTP REST 路由：新增 `POST /gouchen/dredge` 与 `GET /gouchen/snapshot` 路由端点，挂载入 `CORE_ROUTERS`；
  - MCP 扩充至 95 工具里程碑：注册 `gouchen_dredge` 工具规范（支持 `dredge`/`snapshot` 双模式）并挂载处理器 `handle_gouchen_dredge`，保持 `TOOLS` 与 `TOOL_HANDLERS` 1:1 双向对齐；
  - 认知上下文切面：`CognitiveContext` 新增 `gouchen_digest` 属性，在 `to_prompt()` 中渲染结构化钩沉切面（含沉没度展示与樊篱包裹）；
  - 交付端到端不 mock 冒烟测试 `tests/test_gouchen_api_smoke.py`（4 接缝：服务层调度、REST 端点协议往返、MCP 95 工具双向对齐、认知上下文切面渲染全绿）。

## [0.60.0] - 2026-10-30 - 知命（Zhiming · 认知生命全周期演化状态机、动态时效衰变引擎与认知层收敛）

> 版本代号「知命」：出自孔子「五十而知天命」，取「顺应事物生命周期演化、知晓时效规律与认知终局」之意——对应本版主题「认知生命全周期演化状态机、动态时效衰变引擎与认知层收敛，ADR-0081 落地」：突破既有记忆孤立存储、缺乏生命演化与时效衰变机制的局限，构建面向生产环境的一级认知状态机引擎 `ZhimingEngine` 与全生命周期契约。支持多阶段生命流转（`seed` -> `active` -> `decaying` -> `archived` / `dormant` -> `tombstone`）；实现半衰期对数时效衰变与重要性加权评估；行级租户属主安全隔离与樊篱防御；提供 `zhiming_service.py` 服务层统一入口；挂载 HTTP REST 路由（`POST /lifecycle/transition` 与 `GET /lifecycle/inspect`）；MCP 工具池扩充至 **94 工具里程碑**（新增 `zhiming_lifecycle`），实现 `TOOLS` 与 `TOOL_HANDLERS` 1:1 双向一致性守卫；增强认知上下文 `CognitiveContext`，将知命生命状态切面集成至 `zhiming_digest` 中。全量覆盖端到端不 mock 冒烟测试。票据 `.scratch/v0.60-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **核心知命生命状态机引擎 ZhimingEngine 与认知层收敛（票 01，详见 `lantai/cognition/zhiming.py` 与 `tests/test_zhiming_engine_smoke.py`）**：
  - 认知生命全周期状态机：定义 `CognitiveState` 与生命演化契约，实现受控安全状态流转与墓碑封存机制；
  - 动态时效衰变引擎：基于半衰期曲线与交互权重评估记忆新鲜度与衰退指标；
  - 行级租户隔离与安全樊篱防御：跨租户严格防越权，正文安全围栏隔离；
  - 交付端到端不 mock 冒烟测试 `tests/test_zhiming_engine_smoke.py`。
- **知命服务层重构、REST 路由、MCP 94 工具池扩充与认知上下文切面（票 02，详见 `lantai/services/zhiming_service.py`、`lantai/api/routes_zhiming.py`、`lantai/cli/mcp.py` 与 `tests/test_zhiming_api_smoke.py`）**：
  - 服务层封装：提供 `zhiming_transition_service` 与 `zhiming_inspect_service` 统一入口；
  - HTTP REST 路由：新增 `/lifecycle/transition` 与 `/lifecycle/inspect` 路由端点；
  - MCP 扩充至 94 工具里程碑：注册 `zhiming_lifecycle` 工具规范并挂载处理器，保持 `TOOLS` 与 `TOOL_HANDLERS` 1:1 双向对齐；
  - 认知上下文切面：`CognitiveContext` 深度融入 `zhiming_digest`；
  - 交付端到端不 mock 冒烟测试 `tests/test_zhiming_api_smoke.py`。

## [0.59.0] - 2026-10-30 - 札记（Zhaji · 工作区认知暂存便签深度演化、即时上下文熔断与双轨同步闭环）

> 版本代号「札记」：出自古代读书治学摘记要点之木简便签（《文史通义》论治学重札记积累），取「即时留存、随手提炼、去粗存精」之意——对应本版主题「工作区认知暂存便签深度演化、即时上下文熔断与双轨同步闭环，ADR-0080 落地」：突破既有临时便签仅停留在扁平字符串覆盖覆盖、缺乏认知实体契约与结构化分段提炼的局限，构建面向生产环境的一级认知提炼引擎 `ZhajiEngine` 与 `ZhajiItem` / `ZhajiDigestResult` 契约（票 01）。支持多槽位结构化提取（`active_goals`、`key_findings`、`open_questions`、`notes`）；实现平滑字符预算控制与槽位优先保整熔断机制（按槽位优先级 `goals > open_questions > key_findings > notes` 优先保留，严守「宁 miss 不脏写」）；严守行级租户属主安全隔离与樊篱数据围栏（`wrap_as_data`）防提示词注入防御。全面重构服务层 `scratchpad_service.py`，提供 `zhaji_distill_service` 统一入口；挂载 HTTP REST 路由（`POST /scratchpad/distill`）；MCP 工具池扩充至 **93 工具里程碑**（新增 `zhaji_distill` 札记提纯工具），实现 `TOOLS` 与 `TOOL_HANDLERS` 1:1 双向一致性守卫；增强认知上下文 `CognitiveContext`，将工作区提纯便签深度整合至提示词 `zhaji_digest` 切面中（票 02）。全量覆盖端到端不 mock 冒烟测试。票据 `.scratch/v0.59-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **核心札记提炼演化引擎 ZhajiEngine 与认知层收敛（票 01，详见 `lantai/cognition/zhaji.py` 与 `tests/test_zhaji_engine_smoke.py`）**：
  - 札记多槽位结构化领域契约：定义 `ZhajiItem` 与 `ZhajiDigestResult` 契约，输出结构化槽位清单、槽位计数、紧凑文本、预算熔断标记与樊篱数据围栏包裹；
  - 多模式自然文本提纯模型：支持 Markdown 标题行、多前缀列表（`-`、`*`、`•`）与单行管道符（`|`）自然标注提取；
  - 平滑字符预算控制与受控截断：按槽位优先级严格保整截断，耗尽即止，杜绝破损 JSON 与低优先级污染（宁 miss 不脏写）；
  - 行级租户属主 ACL 隔离与樊篱防御：严格校验 `_check_access` 杜绝跨租户会话便签越权查看与覆盖（宁漏不越权），全链路正文采用 `<memory_data>` 围栏隔离；
  - 交付端到端不 mock 冒烟测试 `tests/test_zhaji_engine_smoke.py`（纯函数多槽位解析与紧凑提纯、真实 SQLite DB 会话持久化与流转、平滑字符预算受控保整截断、行级 ACL 隔离与樊篱防注入 4 个独立接缝验证全绿）。
- **札记服务层重构、REST 路由、MCP 93 工具池扩充与认知上下文切面（票 02，详见 `lantai/services/scratchpad_service.py`、`lantai/api/routes_scratchpad.py`、`lantai/cli/mcp.py` 与 `tests/test_zhaji_api_smoke.py`）**：
  - 服务层封装：基于 `ZhajiEngine` 封装 `scratchpad_service.py`，提供 `zhaji_distill_service` 只读与落库统一入口；
  - HTTP REST 路由：新增 `POST /scratchpad/distill` 路由端点；
  - MCP 扩充至 93 工具里程碑：注册 `zhaji_distill` 工具规范定义并挂载处理器，保持 `TOOLS` 与 `TOOL_HANDLERS` 1:1 双向对齐；
  - 认知上下文增强：在 `CognitiveContext` 中引入 `zhaji_digest` 属性，并在 `to_prompt()` 中渲染结构化札记切面（樊篱包裹）；
  - 交付端到端不 mock 冒烟测试 `tests/test_zhaji_api_smoke.py`（服务层调度、REST 端点校验、MCP 93 工具协议往返与对齐、认知切面提示词渲染 4 个独立接缝验证全绿）。

## [0.58.0] - 2026-10-30 - 三省（Sanxing · 认知层反思自省深度演化、因果违例闭环与健康自愈引擎）

> 版本代号「三省」：出自《论语·学而》「吾日三省吾身」，取「审视功过、反思内省、自省自愈」之意——对应本版主题「认知层反思自省深度演化、因果违例闭环与健康自愈引擎，ADR-0079 落地」：突破既有健康诊断仅停留在静态只读扫描、缺乏与认知演化深度联动与自愈闭环的局限，构建面向生产环境的一级自省自愈引擎 `SanxingEngine` 与 `SanxingRemedyResult` 契约（票 01）。引入四大缺陷的主动自愈处置策略（针对被取代陈旧事实的 `supersede_retract` 撤回流转、针对过期条目的 `archive_expire` 自动归档、针对未决冲突的 `arbitrate_dispatch` 标记排查、针对低效碎片的 `demote_deprecate` 降权淘汰）；严守行级租户属主安全隔离与樊篱数据围栏（`wrap_as_data`）防提示词注入防御。全面重构服务层 `sanxing_service.py`，提供 `sanxing_diagnose_service` 与 `sanxing_remedy_service`；挂载 HTTP REST 路由（`GET /sanxing/diagnose`、`POST /sanxing/remedy`）；MCP 工具池扩充至 **92 工具里程碑**（新增 `sanxing_remedy` 自省自愈工具），实现 `TOOLS` 与 `TOOL_HANDLERS` 1:1 双向一致性守卫；增强认知上下文 `CognitiveContext`，将反思自愈深度整合至提示词切面中（票 02）。全量覆盖端到端不 mock 冒烟测试。票据 `.scratch/v0.58-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **核心三省认知反思自省与健康自愈引擎 SanxingEngine（票 01，详见 `lantai/cognition/sanxing.py` 与 `tests/test_sanxing_engine_smoke.py`）**：
  - 自愈策略结构化契约：定义 `SanxingRemedyItem` 与 `SanxingRemedyResult` 契约，输出目标记忆 ID、缺陷类别、建议处置动作、风险等级、执行状态、健康分自愈变迁与樊篱围栏数据包裹；
  - 闭环自愈决策模型：支持陈旧条目撤销、时效过期自动归档、低效碎片降权淘汰，支持预演模式与一键状态机落库执行（宁 miss 不脏写）；
  - 行级租户属主 ACL 隔离与樊篱防御：严格校验 `_check_access` 杜绝越权（宁漏不越权），全链路正文采用 `<memory_data>` 围栏隔离；
  - 交付端到端不 mock 冒烟测试 `tests/test_sanxing_engine_smoke.py`（纯函数缺陷自愈方案生成、真实 SQLite DB 单项自愈落库流转、批量健康自省与报告快照、行级 ACL 隔离与樊篱包裹 4 个独立接缝验证全绿）。
- **三省服务层重构、REST 路由、MCP 92 工具池扩充与认知上下文切面（票 02，详见 `lantai/services/sanxing_service.py`、`lantai/api/routes_sanxing.py`、`lantai/cli/mcp.py` 与 `tests/test_sanxing_api_smoke.py`）**：
  - 服务层封装：基于 `SanxingEngine` 封装 `sanxing_service.py`，提供 `sanxing_diagnose_service` 与 `sanxing_remedy_service` 统一入口；
  - HTTP REST 路由：新增 `GET /sanxing/diagnose` 与 `POST /sanxing/remedy` 路由端点，并挂载入 `CORE_ROUTERS`；
  - MCP 扩充至 92 工具里程碑：注册 `sanxing_remedy` 工具规范定义并挂载处理器，保持 `TOOLS` 与 `TOOL_HANDLERS` 1:1 双向对齐；
  - 认知上下文增强：在 `CognitiveContext` 中引入 `sanxing_digest` 属性，并在 `to_prompt()` 中渲染结构化三省切面（樊篱包裹）；
  - 交付端到端不 mock 冒烟测试 `tests/test_sanxing_api_smoke.py`（服务层调用、REST 端点、MCP 92 工具协议往返与对齐、认知切面提示词渲染 4 个独立接缝验证全绿）。

## [0.57.0] - 2026-10-30 - 考功（Kaogong · 长程价值演化与多维功过评定引擎、生命周期状态机与认知层收敛）

> 版本代号「考功」：出自唐代吏部「考功司」（《唐六典》），掌官吏功过品级考评，取「考校功过、升降品级、去劣存优」之意——对应本版主题「长程价值演化与多维功过评定引擎、生命周期状态机与认知层收敛，ADR-0078 落地」：突破既有价值升降级仅依赖简单引用次数硬编码阈值的局限，构建面向生产环境的一级认知引擎 `KaogongEngine` 与 `KaogongResult` 契约（票 01）。引入综合采纳率（`helpful_score`）、使用频度密度（`density_score`）、衰减与冲突风险多因子复合评分（`MeritScore`），驱动生命周期状态机精准流转（`promote_longterm` 晋升长期语义层并免疫衰减、`demote_deprecate` 劣质记忆降权淘汰、`keep_neutral` 样本不足审慎保持原状，宁 miss 不脏写）；严守行级租户属主安全隔离与樊篱数据围栏（`wrap_as_data`）防提示词注入防御。全面重构服务层 `kaogong_service.py`，保持历史评级接口向下兼容并增补 `kaogong_inspect_service` 深度考评；挂载 HTTP REST 路由（`POST /kaogong/eval`、`GET /kaogong/report`、`POST /kaogong/inspect`）；MCP 工具池扩充至 **91 工具里程碑**（新增 `kaogong_inspect` 细粒度审查工具），实现 `TOOLS` 与 `TOOL_HANDLERS` 1:1 双向一致性守卫；增强认知上下文 `CognitiveContext`，将价值演化评定深度整合至提示词切面中（票 02）。全量覆盖端到端不 mock 冒烟测试。票据 `.scratch/v0.57-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **核心考功价值演化评定引擎 KaogongEngine 与认知层收敛（票 01，详见 `lantai/cognition/kaogong.py` 与 `tests/test_kaogong_engine_smoke.py`）**：
  - 价值评定结构化契约：定义 `KaogongResult` 契约，输出记忆 ID、处置动作、综合评分、使用量/采纳量、因子分解映射、生命周期层级流转、重要度变迁、裁决理由与樊篱数据包裹；
  - 多因子复合打分模型：综合评定采纳率表现与使用频度密度，支持高价值沉淀为恒定资产、劣质条目自动淡出，并在样本不足（use_count < 3）时审慎保持原状；
  - 单条考评与批量巡检持久化：支持单条记忆即时考评与批量 `run_cycle`，落库维护 `tier`、`decay_class` 与 `importance` 并产出结构化审计报告快照；
  - 行级租户属主 ACL 隔离与樊篱防御：严格校验 `_check_access` 杜绝跨租户改写（宁漏不越权），全链路正文采用 `<memory_data>` 围栏隔离；
  - 交付端到端不 mock 冒烟测试 `tests/test_kaogong_engine_smoke.py`（纯函数多因子评分、SQLite 单条考评落库流转、批量巡检报告快照、行级 ACL 隔离与樊篱防御 4 个独立接缝验证全绿）。
- **考功服务层重构、REST 路由、MCP 91 工具池扩充与认知上下文切面（票 02，详见 `lantai/services/kaogong_service.py`、`lantai/api/routes_kaogong.py`、`lantai/cli/mcp.py` 与 `tests/test_kaogong_api_smoke.py`）**：
  - 服务层重构：统一基于 `KaogongEngine` 重构 `kaogong_service.py`，保持既有 `evaluate_memory_item_grade` 与 `run_kaogong_cycle` 契约兼容，增设 `kaogong_inspect_service` 深度审查；
  - HTTP REST 路由：新增 `POST /kaogong/eval`、`GET /kaogong/report` 与 `POST /kaogong/inspect` 路由端点，并挂载入 `CORE_ROUTERS`；
  - MCP 扩充至 91 工具里程碑：注册 `kaogong_inspect` 工具规范定义并挂载处理器，保持 `TOOLS` 与 `TOOL_HANDLERS` 1:1 双向对齐；
  - 认知上下文增强：在 `CognitiveContext` 中引入 `kaogong_digest` 属性，并在 `to_prompt()` 中渲染结构化考功切面（樊篱包裹）；
  - 交付端到端不 mock 冒烟测试 `tests/test_kaogong_api_smoke.py`（服务层调用、REST 端点、MCP 91 工具协议往返与对齐、认知切面提示词渲染 4 个独立接缝验证全绿）。

## [0.56.0] - 2026-10-30 - 披沙（Pisha · 候选记忆递归提纯去噪、多轨指代消解与认知引擎收敛）

> 版本代号「披沙」：出自《世说新语·德行》「披沙拣金，往往见宝」，取「去粗取精、沙中淘金、去噪提纯」之意——对应本版主题「候选记忆递归提纯去噪、多轨指代消解与认知引擎收敛，ADR-0077 落地」：突破早期候选精炼工具分散脆弱、缺少结构化审计契约与降级保障的局限，构建面向生产环境的一级认知引擎 `PishaEngine` 与 `PishaResult` 契约（票 01）。引入两阶段确定性规则管线（启发式口语化副词助词修剪 `colloquial_pruned`、基于实体提示的确定性代词消解 `pronoun_resolutions`），并在 LLM 异常时提供严格确定性保底（纯噪音直接归零拒绝，有规则收益时平滑校准，宁 miss 不脏写）；严守行级租户属主安全隔离与樊篱数据围栏（`wrap_as_data`）防提示词注入防御。全面重构服务层 `refine_service.py`，新增统一入口 `pisha_refine_service` 并保持历史向下兼容；挂载 HTTP REST 路由（`POST /pisha/refine`、`POST /pisha/batch`）；MCP 工具池扩充至 **90 工具里程碑**（新增 `pisha_refine`），实现 `TOOLS` 与 `TOOL_HANDLERS` 1:1 双向一致性守卫；增强认知上下文 `CognitiveContext`，将提纯结果深度整合至提示词切面中（票 02）。全量覆盖端到端不 mock 冒烟测试。票据 `.scratch/v0.56-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **核心披沙提纯去噪引擎 PishaEngine 与认知层收敛（票 01，详见 `lantai/cognition/pisha.py` 与 `tests/test_pisha_engine_smoke.py`）**：
  - 提纯去噪结构化契约：定义 `PishaResult` 契约，输出原始/精炼文本、原置信度与重估置信度、有效性标记、辨域划分、指代消解映射、去噪标签、判定理由与樊篱围栏内容；
  - 确定性与启发式两阶段清洗：在 LLM 调用前执行口语噪音过滤与实体提示词典消解，并在 LLM 失败时自动降级保底，避免因外部模型抖动脏写或丢失候选；
  - 候选记录落库与生命周期联动：支持单条与批量候选扫描，纯噪音自动置为 `rejected` 归档，高置信提纯条目平滑更新至候选池并注入审计线索；
  - 行级租户属主 ACL 隔离与樊篱防御：严格校验 `_check_access` 杜绝越权（宁漏不越权），全链路正文采用 `<memory_data>` 围栏隔离；
  - 交付端到端不 mock 冒烟测试 `tests/test_pisha_engine_smoke.py`（启发式与确定性实体消解、SQLite 单条记录更新与状态流转、批量模糊区间扫描、行级 ACL 隔离与樊篱防御 4 个独立接缝验证全绿）。
- **披沙服务层重构、REST 路由、MCP 90 工具池扩充与认知上下文切面（票 02，详见 `lantai/services/refine_service.py`、`lantai/api/routes_pisha.py`、`lantai/cli/mcp.py` 与 `tests/test_pisha_api_smoke.py`）**：
  - 服务层重构：统一基于 `PishaEngine` 实现 `refine_memory_text`、`refine_candidate_record`、`batch_refine_candidates`，并提供 `pisha_refine_service` 统一入口；
  - HTTP REST 路由：新增 `POST /pisha/refine` 与 `POST /pisha/batch` 路由端点，并挂载入 `CORE_ROUTERS`；
  - MCP 扩充至 90 工具里程碑：注册 `pisha_refine` 工具规范定义并挂载处理器，保持 `TOOLS` 与 `TOOL_HANDLERS` 1:1 双向对齐；
  - 认知上下文增强：在 `CognitiveContext` 中引入 `pisha_digest` 属性，并在 `to_prompt()` 中渲染结构化提纯切面（樊篱包裹）；
  - 交付端到端不 mock 冒烟测试 `tests/test_pisha_api_smoke.py`（服务层调用、REST 端点、MCP 90 工具协议往返与对齐、认知切面提示词渲染 4 个独立接缝验证全绿）。

> 版本代号「拾遗」：出自唐代谏官官职「拾遗」（杜甫曾任左拾遗），取「捡回遗漏之物、补苴罅漏」之意——对应本版主题「主流水线零召回自适应补救闭环、检索召回深度治理与端到端补救切面融合，ADR-0076 落地」：突破既有检索主流水线在初始初筛未命中或经衰减/时效/辨域过滤后出现零召回（Zero-Recall）直接返空的局限，构建面向生产环境的主流水线零召回自适应拾遗补救闭环（票 01）。在 `hybrid_search` 中原生打通 `ShiyiEngine.remedy_search_pipeline`，在零召回时自适应唤醒宽松分词、字符级子序列与辨域安全放宽补救阶梯；补救条目通过统一契约 `to_search_result()` 转换为标准检索结果项，携带 `is_remedied: True`、`remedy_tier` 与 `remedy_reason`；支持受控兼容开关 `enable_shiyi`，关闭时 100% 保持旧有静默返回行为；全链路补救正文强制经由樊篱（`wrap_as_data`）数据围栏包裹，严密防御提示注入，并严守行级租户属主安全隔离纪律（宁漏不越权）。打通服务层 `remedy_pipeline_service`、HTTP REST 路由（`POST /shiyi/pipeline`）、MCP 工具（`shiyi_pipeline`，工具池扩充至 89 个里程碑且 1:1 双向对齐）与认知上下文 `CognitiveContext`（票 02，拾遗补救切面结构化呈现）。全量覆盖端到端不 mock 冒烟测试。票据 `.scratch/v0.55-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **核心主流水线零召回自适应拾遗补救闭环与 ShiyiEngine 增强（票 01，详见 `lantai/retrieval/hybrid.py`、`lantai/cognition/shiyi.py` 与 `tests/test_shiyi_pipeline_smoke.py`）**：
  - 拾遗候选统一度量投影：在 `RemedyCandidate` 契约中扩展 `to_search_result()`，将补救候选无缝标准化为 `hybrid_search` 结果项契约，携带分值、层级与补救元数据；
  - 主流水线自适应闭环：在 `hybrid_search` 与 `_keyword_fallback` 零召回截断点原生接入 `ShiyiEngine.remedy_search_pipeline`，实现初筛/过滤后空结果的主动自适应补救召回；
  - 审计线索留痕：在 `trace_steps` 中增加 `shiyi_remedy` 诊断步骤，完整记录补救候选条数、耗时与分值区间；
  - 兼容回退与 ACL 守卫：引入 `enable_shiyi=True` 可控开关，默认自适应唤醒，显式关闭时保持旧有空返回行为；补救全流程严守行级租户属主隔离与樊篱防注入（宁漏不越权）；
  - 交付端到端不 mock 冒烟测试 `tests/test_shiyi_pipeline_smoke.py`（主流水线零召回自适应唤醒、补救投影与 trace 记录对齐、开关关闭兼容回退、行级 ACL 隔离与樊篱包裹 4 个独立接缝验证全绿）。
- **拾遗主流水线服务层、REST 路由、MCP 89 工具池扩充与认知上下文切面（票 02，详见 `lantai/services/shiyi_service.py`、`lantai/api/routes_shiyi.py`、`lantai/cli/mcp.py` 与 `tests/test_shiyi_pipeline_api_smoke.py`）**：
  - 服务层封装：新增 `remedy_pipeline_service`，统一调度底层 `hybrid_search` 与 DB 会话生命周期；
  - HTTP REST 路由：新增 `POST /shiyi/pipeline` 鉴权端点，挂载入 `routes_shiyi_router`；
  - MCP 工具池扩容至 89 工具里程碑：注册 `shiyi_pipeline` 工具并实现 `handle_shiyi_pipeline` 处理器，`TOOLS` 与 `TOOL_HANDLERS` 1:1 双向一致性守卫全绿；
  - 认知上下文增强：`CognitiveContext.to_prompt()` 深度整合拾遗补救切面渲染（樊篱包裹）；
  - 交付端到端不 mock 冒烟测试 `tests/test_shiyi_pipeline_api_smoke.py`（服务层调用、REST 端点、MCP 协议往返与 89 工具对齐、认知上下文切面渲染 4 个独立接缝验证全绿）。

## [0.54.0] - 2026-10-30 - 青鸟（Qingniao · 多智能体记忆信使与异步通信路由引擎）

> 版本代号「青鸟」：出自李商隐《无题》「蓬山此去无多路，青鸟殷勤为探看」与《山海经》西王母三青鸟，代指在认知联邦与智能体集群生态中专职传递核心记忆与书简公文的神圣信使——对应本版主题「多智能体记忆信使与异步通信路由引擎、记忆信函封装、送达回执闭环与认知上下文青鸟切面，ADR-0075 落地」：突破既有记忆局限于单体本地或扁平共享表的局限，构建面向多智能体协作与联邦认知的高可靠青鸟信使引擎 `QingniaoEngine`（票 01）。支持跨智能体记忆信函封装与寻址（`QingniaoLetter`），提供异步派发回执（`QingniaoDispatchResult`）；支持收件箱智能拉取与优先级倒序调度（`fetch_inbox`）；实现生命周期签收闭环（`acknowledge_letter`，支持 ACK/REJECT 状态机转换）；全链路信函正文强制经由樊篱（`wrap_as_data`）数据围栏包裹，抵御跨体提示词注入攻击，并严格校验租户隔离与接收方签收权限（宁 miss 不越权）。打通服务层 `qingniao_service.py`、HTTP REST 路由（`POST /qingniao/dispatch`、`GET /qingniao/inbox`、`POST /qingniao/ack`）、MCP 工具（`qingniao_messenger`，工具池扩充至 88 个里程碑且 1:1 双向对齐）与认知上下文 `CognitiveContext`（票 02，青鸟多智能体信使切面结构化呈现）。全量覆盖端到端不 mock 冒烟测试。票据 `.scratch/v0.54-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **核心青鸟多智能体记忆信使与异步通信路由引擎 QingniaoEngine（票 01，详见 `lantai/cognition/qingniao.py` 与 `tests/test_qingniao_engine_smoke.py`）**：
  - 记忆信函契约：定义 `QingniaoLetter` 契约，统一纳管信函 ID、发信方、收信方（支持单播与广播）、主题、正文、优先级（high/normal/low）、状态与时序时间戳；
  - 派发回执契约：定义 `QingniaoDispatchResult`，输出入队派发状态、信函 ID、受众数量与时间戳；
  - 核心派发与信箱调度：实现 `dispatch_letter` 与 `fetch_inbox`，支持多智能体异步投递、按优先级排序与状态过滤；
  - 签收回执状态机：实现 `acknowledge_letter`，支持 ACK 确认与 REJECT 拒收，并限定仅目标接收方或管理员有权操作；
  - 樊篱安全隔离与行级 ACL：信函正文强制包裹 `<memory_data>` 围栏，严守租户边界与跨体权限隔离（宁 miss 不脏写）；
  - 交付端到端不 mock 冒烟测试 `tests/test_qingniao_engine_smoke.py`（信函派发与入队回执、收信箱拉取与 ACK 生命周期闭环、樊篱防注入围栏、行级 ACL 隔离与非法越权拦截 4 个独立接缝验证全绿）。
- **青鸟服务层、REST 路由、MCP 88 工具池扩充与认知上下文切面（票 02，详见 `lantai/services/qingniao_service.py`、`lantai/api/routes_qingniao.py`、`lantai/cli/mcp.py`、`lantai/cognition/context.py` 与 `tests/test_qingniao_api_smoke.py`）**：
  - 服务层封装：新增 `dispatch_letter_service`、`fetch_inbox_service` 与 `acknowledge_letter_service`，统一管理 DB 会话生命周期与信使调度；
  - HTTP REST 路由：新增 `POST /qingniao/dispatch`、`GET /qingniao/inbox` 与 `POST /qingniao/ack` 鉴权端点，挂载入 `app.py` 核心鉴权路由；
  - MCP 工具池扩容至 88 工具里程碑：注册 `qingniao_messenger` 工具并实现 `handle_qingniao_messenger` 处理器，`TOOLS` 与 `TOOL_HANDLERS` 1:1 双向一致性守卫全绿；
  - 认知上下文增强：`CognitiveContext.to_prompt()` 引入 `qingniao_digest` 切面，结构化渲染跨智能体待收公文与信使信函（樊篱包裹）；
  - 交付端到端不 mock 冒烟测试 `tests/test_qingniao_api_smoke.py`（服务层调用、REST 端点、MCP 协议往返与 88 工具对齐、认知上下文青鸟切面 4 个独立接缝验证全绿）。

## [0.53.0] - 2026-10-30 - 石室（Shishi · 高密核心记忆金匮封存与防篡改硬核锚定引擎）

> 版本代号「石室」：出自西汉司马迁《史记·太史公自序》「迁为太史令，紬石室金匮之书」，喻指在认知架构中将智能体最核心、最神圣不可动摇之身份、原则与铁律，作为「金匮石室」秘籍硬核封存，施加不可变锁定，提供全生命周期防篡改、完整性验真与樊篱隔离锚定——对应本版主题「高密核心记忆金匮封存与防篡改硬核锚定引擎、SHA-256 完整性验真签名、不可变加锁与认知上下文石室切面，ADR-0074 落地」：突破既有核心记忆停留于普通数据行、缺乏防篡改与完整性验真保障的局限，构建面向生产环境的高性能石室封存验真引擎 `ShishiEngine`（票 01）。支持针对核心身份（identity）、行为铁律（policy）与基石任务（task）执行金匮封存与版本自增（`seal_core_memory`），计算 SHA-256 完整性摘要并施加不可变保护（`is_immutable`）；支持全域完整性哈希验真（`verify_integrity`），主动识别并拦截底层非法数据篡改；支持安全读取已封存的核心硬核记忆（`get_sealed_blocks`）；全链路封存正文强制经由樊篱（`wrap_as_data`）数据围栏包裹，严密防御提示注入，并严守行级租户属主安全隔离纪律（宁 miss 不脏写）。打通服务层 `shishi_service.py`、HTTP REST 路由（`POST /shishi/seal`、`GET /shishi/verify`、`GET /shishi/blocks`）、MCP 工具（`shishi_vault`，工具池扩充至 87 个里程碑且 1:1 双向对齐）与认知上下文 `CognitiveContext`（票 02，石室金匮硬核切面结构化呈现）。全量覆盖端到端不 mock 冒烟测试。票据 `.scratch/v0.53-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **核心石室高密核心记忆金匮封存与防篡改硬核锚定引擎 ShishiEngine（票 01，详见 `lantai/cognition/shishi.py` 与 `tests/test_shishi_engine_smoke.py`）**：
  - 金匮封存块契约：定义 `ShishiVaultBlock` 契约，统一纳管块名、正文、SHA-256 哈希签名、不可变标识与递增版本号，支持樊篱安全渲染；
  - 完整性验真报告契约：定义 `IntegrityVerificationResult`，输出验真合格状态、受检块数与篡改异常清单；
  - 金匮封存与不可变锁定：实现 `seal_core_memory`，支持核心记忆版本递增与哈希签名，已锁定时非管理员修改强制抛出权限异常；
  - 完整性哈希比对验真：实现 `verify_integrity`，全域扫描当前租户内核心块哈希指纹，精准定位并报告底层非法篡改；
  - 樊篱安全隔离与行级 ACL：核心封存正文强制施加 `<memory_data>` 围栏隔离，严格校验租户与用户属主（宁 miss 不脏写）；
  - 交付端到端不 mock 冒烟测试 `tests/test_shishi_engine_smoke.py`（金匮封存与版本递增、底层篡改主动感知与验真、樊篱包裹防注入、行级 ACL 隔离与不可变锁定 4 个独立接缝验证全绿）。
- **石室服务层、REST 路由、MCP 87 工具池扩充与认知上下文切面（票 02，详见 `lantai/services/shishi_service.py`、`lantai/api/routes_shishi.py`、`lantai/cli/mcp.py`、`lantai/cognition/context.py` 与 `tests/test_shishi_api_smoke.py`）**：
  - 服务层封装：新增 `seal_core_memory_service`、`verify_shishi_integrity_service` 与 `get_sealed_blocks_service`，统一管理 DB 会话生命周期与石室调度；
  - HTTP REST 路由：新增 `POST /shishi/seal`、`GET /shishi/verify` 与 `GET /shishi/blocks` 鉴权端点，挂载入 `app.py` 核心鉴权路由；
  - MCP 工具池扩容至 87 工具里程碑：注册 `shishi_vault` 工具并实现 `handle_shishi_vault` 处理器，`TOOLS` 与 `TOOL_HANDLERS` 1:1 双向一致性守卫全绿；
  - 认知上下文增强：`CognitiveContext.to_prompt()` 引入 `shishi_digest` 切面，结构化渲染核心记忆金匮封存块（樊篱包裹）；
  - 交付端到端不 mock 冒烟测试 `tests/test_shishi_api_smoke.py`（服务层调用、REST 端点、MCP 协议往返与 87 工具对齐、认知上下文石室切面 4 个独立接缝验证全绿）。

## [0.52.0] - 2026-10-30 - 测候（Cehou · 影子观察期巡检与演化状态监控引擎）

> 版本代号「测候」：出自北宋沈括《梦溪笔谈·象数一》「司天监测候天象，待其应验，以定历法」，喻指在认知演化提案或新规则生效前，作为专职影子期观测与状态机评估引擎，持续测验守候、跟踪指标漂移、验证收益真实性，待其数据周全应验后再行转正入库——对应本版主题「影子观察期巡检与演化状态监控引擎、应验转正与漂移熔断裁决、样本实测度量与认知上下文测候切面，ADR-0073 落地」：突破既有观察期缺乏针对认知演化视窗的实时巡检、胜率聚合与生命周期状态机管理的局限，构建面向生产环境的高性能影子观察巡检引擎 `CehouEngine`（票 01）。支持针对参数建议、反思提案与新规则创建影子视窗（`create_observation_window`）；支持持续采集并度量实测运行样本（`record_sample`），统计时延与收益分差；支持巡检并自动裁决视窗状态跃迁（`inspect_window`），胜率与样本达标自动判定为应验转正（`graduated`），异常劣化判定为熔断回滚（`rolled_back`）；支持全域视窗扫描与告警汇总（`scan_active_windows`）；全链路观测摘要与转正裁决强制经由樊篱（`wrap_as_data`）数据围栏包裹，严密防御提示注入，并严守行级租户属主安全隔离纪律（宁 miss 不脏写）。打通服务层 `cehou_service.py`、HTTP REST 路由（`GET /cehou/windows`、`POST /cehou/inspect`、`POST /cehou/sample`、`POST /cehou/windows`）、MCP 工具（`cehou_observe`，工具池扩充至 86 个里程碑且 1:1 双向对齐）与认知上下文 `CognitiveContext`（票 02，测候观察切面结构化呈现）。全量覆盖端到端不 mock 冒烟测试。票据 `.scratch/v0.52-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **核心测候影子观察期巡检与健康判定引擎 CehouEngine（票 01，详见 `lantai/cognition/cehou.py` 与 `tests/test_cehou_engine_smoke.py`）**：
  - 影子视窗契约：定义 `ShadowObservation` 契约，统一纳管视窗标识、目标类型、达标天数与样本要求、实测胜率与时延分差，支持樊篱安全渲染；
  - 巡检汇总报告契约：定义 `InspectionReport`，输出视窗状态分布与风险告警列表；
  - 运行样本追加与胜率核算：实现 `record_sample`，支持实时样本上报与异常错误率激增检测；
  - 应验转正与回滚状态机：实现 `inspect_window`，自动判定天数、样本量与胜率达标状态；
  - 樊篱安全隔离与行级 ACL：视窗观测摘要强制施加 `<memory_data>` 围栏隔离，严格校验租户与用户属主（宁 miss 不脏写）；
  - 交付端到端不 mock 冒烟测试 `tests/test_cehou_engine_smoke.py`（影子视窗指标核算与转正判定、活跃视窗巡检与报警、樊篱包裹防注入、行级 ACL 隔离 4 个独立接缝验证全绿）。
- **测候服务层、REST 路由、MCP 86 工具池扩充与认知上下文切面（票 02，详见 `lantai/services/cehou_service.py`、`lantai/api/routes_cehou.py`、`lantai/cli/mcp.py`、`lantai/cognition/context.py` 与 `tests/test_cehou_api_smoke.py`）**：
  - 服务层封装：新增 `inspect_cehou_window_service`、`scan_cehou_windows_service`、`record_cehou_sample_service` 与 `create_cehou_window_service`，统一管理 DB 会话生命周期与视窗调度；
  - HTTP REST 路由：新增 `POST /cehou/windows`、`POST /cehou/inspect`、`GET /cehou/windows` 与 `POST /cehou/sample` 鉴权端点，挂载入 `app.py` 核心鉴权路由；
  - MCP 工具池扩容至 86 工具里程碑：注册 `cehou_observe` 工具并实现 `handle_cehou_observe` 处理器，`TOOLS` 与 `TOOL_HANDLERS` 1:1 双向一致性守卫全绿；
  - 认知上下文增强：`CognitiveContext.to_prompt()` 引入 `cehou_digest` 切面，结构化渲染影子期视窗与转正状态（樊篱包裹）；
  - 交付端到端不 mock 冒烟测试 `tests/test_cehou_api_smoke.py`（服务层调用、REST 端点、MCP 协议往返与 86 工具对齐、认知上下文测候切面 4 个独立接缝验证全绿）。

## [0.51.0] - 2026-10-30 - 底本（Diben · 跨会话现场定本与认知状态传承引擎）

> 版本代号「底本」：出自章学诚《校讎通义》「校讎之法，必本于底本」，喻指在会话间隙或任务更迭时，将认知现场固化为唯一可信定本，为新一轮交互提供定本锚点与传承依据，保证 Agent 行事有据、不迷失方向——对应本版主题「跨会话现场定本与认知状态传承引擎、意图漂移检测、会话定本锚定与认知上下文底本切面，ADR-0072 落地」：突破既有会话快照停留于标量存储、缺乏认知推理沉淀与意图漂移判别的局限，构建面向生产环境的高性能跨会话定本传承引擎 `DibenEngine`（票 01）。支持针对会话五段现场（在做/下一步/工作区/决策/待办）进行结构化定本提炼与固化（`synthesize_anchor`）；支持实时比对用户新输入与激活底本的语义重合与意图偏转程度（`analyze_drift`），精准输出意图延续与漂移评级（`none` / `slight` / `significant`）；支持安全检索当前租户激活底本（`get_active_anchor`）；全链路底本定本与漂移分析推论强制经由樊篱（`wrap_as_data`）数据围栏包裹，严密防御提示注入，并严守行级租户属主安全隔离纪律（宁 miss 不脏写）。打通服务层 `diben_service.py`、HTTP REST 路由（`POST /diben/anchor`、`POST /diben/drift`、`GET /diben/latest`）、MCP 工具（`diben_anchor`，工具池扩充至 85 个里程碑且 1:1 双向对齐）与认知上下文 `CognitiveContext`（票 02，底本定本切面结构化呈现）。全量覆盖端到端不 mock 冒烟测试。票据 `.scratch/v0.51-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **核心底本定本锚定与意图漂移分析引擎 DibenEngine（票 01，详见 `lantai/cognition/diben.py` 与 `tests/test_diben_engine_smoke.py`）**：
  - 定本模型契约：定义 `SessionAnchor` 契约，统一纳管在做意图、下一步、当前工作区、关键决策列表与未竟待办，支持樊篱安全渲染；
  - 意图漂移分析契约：定义 `DriftAnalysis`，输出意图延续得分 `continuity_score`、延续布尔标识与漂移等级；
  - 认知定本提炼与落库：实现 `synthesize_anchor`，与底层 `SessionCheckpoint` 存储平滑兼容并强化认知提炼；
  - 意图延续度计算：实现 `analyze_drift`，基于结巴分词与关键实词匹配，判别新输入是否偏离激活底本；
  - 樊篱安全隔离与行级 ACL：底本定本与漂移推论强制施加 `<memory_data>` 围栏隔离，严格校验租户与用户属主（宁 miss 不脏写）；
  - 交付端到端不 mock 冒烟测试 `tests/test_diben_engine_smoke.py`（定本提炼与结构化提取、意图延续与漂移分析、樊篱包裹防注入、行级 ACL 隔离 4 个独立接缝验证全绿）。
- **底本服务层、REST 路由、MCP 85 工具池扩充与认知上下文切面（票 02，详见 `lantai/services/diben_service.py`、`lantai/api/routes_diben.py`、`lantai/cli/mcp.py`、`lantai/cognition/context.py` 与 `tests/test_diben_api_smoke.py`）**：
  - 服务层封装：新增 `synthesize_anchor_service`、`analyze_drift_service` 与 `get_active_anchor_service`，统一管理 DB 会话生命周期与定本调度；
  - HTTP REST 路由：新增 `POST /diben/anchor`、`POST /diben/drift` 与 `GET /diben/latest` 鉴权端点，挂载入 `app.py` 核心鉴权路由；
  - MCP 工具池扩容至 85 工具里程碑：注册 `diben_anchor` 工具并实现 `handle_diben_anchor` 处理器，`TOOLS` 与 `TOOL_HANDLERS` 1:1 双向一致性守卫全绿；
  - 认知上下文增强：`CognitiveContext.to_prompt()` 引入 `diben_digest` 切面，结构化渲染跨会话定本快照（樊篱包裹）；
  - 交付端到端不 mock 冒烟测试 `tests/test_diben_api_smoke.py`（服务层调用、REST 端点、MCP 协议往返与 85 工具对齐、认知上下文底本切面 4 个独立接缝验证全绿）。

## [0.50.0] - 2026-10-30 - 拟议（Niyi · 认知演化提案审议与因果变化推演引擎）

> 版本代号「拟议」：出自《易·系辞上》「拟之而后言，议之而后动，拟议以成其变化」，喻指在认知发生实质变更前，先作周详模拟推演，再行审议评估，最终成其健全之认知演化——对应本版主题「认知演化提案审议与因果变化推演引擎、因果拓扑断裂风险评级、未决提案互斥审计与认知上下文拟议切面，ADR-0071 落地」：突破既有演化模块点对点孤立变更、缺乏拓扑全局因果推演与未决提案冲突排查的局限，构建面向生产环境的高性能演化审议推演引擎 `NiyiEngine`（票 01）。支持针对任意提议的认知变更（`add` / `update` / `merge` / `deprecate`）执行因果拓扑影响推演（`deliberate_proposal`），模拟对相连 `MemoryEdge` 因果边的冲击，精准输出风险评级（`low` / `medium` / `high`）与结构化审议结论（`DeliberationOutcome`）；支持当前租户范围内未决提案池的并发竞争与互斥审计（`audit_pending_proposals`），自动发现针对同一目标的冲突提案对；全链路审议推论与审计摘要强制经由樊篱（`wrap_as_data`）数据围栏包裹，严密防御提示注入，并严守行级租户属主安全隔离纪律（宁 miss 不脏写）。打通服务层 `niyi_service.py`、HTTP REST 路由（`POST /niyi/deliberate`、`GET /niyi/audit`）、MCP 工具（`niyi_deliberate`，工具池扩充至 84 个里程碑且 1:1 双向对齐）与认知上下文 `CognitiveContext`（票 02，拟议审议切面结构化呈现）。全量覆盖端到端不 mock 冒烟测试。票据 `.scratch/v0.50-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **核心拟议因果推演与结构化提案引擎 NiyiEngine（票 01，详见 `lantai/cognition/niyi.py` 与 `tests/test_niyi_engine_smoke.py`）**：
  - 审议推演模型：定义 `DeliberationOutcome` 契约，统一纳管打补丁前后状态快照、关联因果边冲击详情、风险等级与审议理由，支持樊篱安全渲染；
  - 提案因果拓扑推演：实现 `deliberate_proposal`，模拟废弃/更新对下游因果链条的影响，高风险操作前瞻预警；
  - 未决提案冲突审计：实现 `audit_pending_proposals`，全域扫描当前租户内未决提案，识别互斥竞争对并输出结构化审计摘要；
  - 樊篱安全隔离与行级 ACL：审议推论与审计摘要强制施加 `<memory_data>` 围栏隔离，严格校验租户与用户属主（宁 miss 不脏写）；
  - 交付端到端不 mock 冒烟测试 `tests/test_niyi_engine_smoke.py`（提案变更因果影响推演、未决提案互斥审计、樊篱包裹防注入、行级 ACL 隔离 4 个独立接缝验证全绿）。
- **拟议服务层、REST 路由、MCP 84 工具池扩充与认知上下文切面（票 02，详见 `lantai/services/niyi_service.py`、`lantai/api/routes_niyi.py`、`lantai/cli/mcp.py`、`lantai/cognition/context.py` 与 `tests/test_niyi_api_smoke.py`）**：
  - 服务层封装：新增 `deliberate_proposal_service` 与 `audit_proposals_service`，统一管理 DB 会话生命周期与演化推演调度；
  - HTTP REST 路由：新增 `POST /niyi/deliberate` 与 `GET /niyi/audit` 鉴权端点，挂载入 `app.py` 核心鉴权路由；
  - MCP 工具池扩容至 84 工具里程碑：注册 `niyi_deliberate` 工具并实现 `handle_niyi_deliberate` 处理器，`TOOLS` 与 `TOOL_HANDLERS` 1:1 双向一致性守卫全绿；
  - 认知上下文增强：`CognitiveContext.to_prompt()` 引入 `niyi_digest` 切面，结构化渲染提案演化推论与风险评级（樊篱包裹）；
  - 交付端到端不 mock 冒烟测试 `tests/test_niyi_api_smoke.py`（服务层调用、REST 端点、MCP 协议往返与 84 工具对齐、认知上下文拟议切面 4 个独立接缝验证全绿）。

## [0.49.0] - 2026-10-30 - 校雠（Jiaochou · 智能语义去重与知识对齐订误引擎）

> 版本代号「校雠」：出自西汉刘向《别录》「一人读书，校其上下，得谬误而差正之，谓之校；一人持本，一人读书，若冤家相对，谓之雠」，喻指多源互校、勘误订正与语义去重——对应本版主题「智能语义去重与知识对齐订误引擎、双源对齐校勘、存量冗余扫描与认知上下文校雠切面，ADR-0070 落地」：突破既有仅在候选入库瞬间进行单一余弦去重的局限，构建面向生产环境的高性能校勘对齐引擎 `JiaochouEngine`（票 01）。支持针对任意两段记忆/事实执行细粒度语义对齐校勘（`compare_and_collate`），精准抽取共同事实与增量分歧点，输出结构化订误动作建议（`merge` / `update` / `retain_both`）；支持存量全库活跃记忆的近义冗余聚类扫描（`scan_redundancy`）；全链路融合正文与差异总结强制经由樊篱（`wrap_as_data`）数据围栏包裹，严密防御提示注入，并严守行级租户属主安全隔离纪律（宁 miss 不脏写）。打通服务层 `jiaochou_service.py`、HTTP REST 路由（`POST /jiaochou/collate`、`GET /jiaochou/scan`）、MCP 工具（`jiaochou_collate`，工具池扩充至 83 个里程碑且 1:1 双向对齐）与认知上下文 `CognitiveContext`（票 02，校雠对齐切面结构化呈现）。全量覆盖端到端不 mock 冒烟测试。票据 `.scratch/v0.49-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **核心校雠语义去重与知识对齐引擎 JiaochouEngine（票 01，详见 `lantai/cognition/jiaochou.py` 与 `tests/test_jiaochou_engine_smoke.py`）**：
  - 校勘对齐模型：定义 `CollationDiff` 契约，统一纳管重合词条、差异分歧摘要与融合建议提案，支持樊篱安全渲染；
  - 双源事实对齐算法：实现 `compare_and_collate`，支持多粒度分词重合度计算、增量细节提炼与订误动作推荐；
  - 存量库冗余扫描：实现 `scan_redundancy`，多对高阶聚类扫描与近义冗余记忆簇识别；
  - 樊篱安全隔离与行级 ACL：校勘融合提案强制施加 `<memory_data>` 围栏隔离，严格校验租户与用户属主；
  - 交付端到端不 mock 冒烟测试 `tests/test_jiaochou_engine_smoke.py`（双源事实对齐差异提取、存量冗余扫描、樊篱包裹防注入、行级 ACL 隔离 4 个独立接缝验证全绿）。
- **校雠服务层、REST 路由、MCP 83 工具池扩充与认知上下文切面（票 02，详见 `lantai/services/jiaochou_service.py`、`lantai/api/routes_jiaochou.py`、`lantai/cli/mcp.py`、`lantai/cognition/context.py` 与 `tests/test_jiaochou_api_smoke.py`）**：
  - 服务层封装：新增 `collate_memories_service` 与 `scan_redundancy_service`，统一管理 DB 会话生命周期与校勘参数调度；
  - HTTP REST 路由：新增 `POST /jiaochou/collate` 与 `GET /jiaochou/scan` 鉴权端点，挂载入 `app.py` 核心鉴权路由；
  - MCP 工具池扩容至 83 工具里程碑：注册 `jiaochou_collate` 工具并实现 `handle_jiaochou_collate` 处理器，`TOOLS` 与 `TOOL_HANDLERS` 1:1 双向一致性守卫全绿；
  - 认知上下文增强：`CognitiveContext.to_prompt()` 引入 `jiaochou_digest` 切面，结构化渲染校勘建议与相似度标尺（樊篱包裹）；
  - 交付端到端不 mock 冒烟测试 `tests/test_jiaochou_api_smoke.py`（服务层调用、REST 端点、MCP 协议往返与 83 工具对齐、认知上下文校勘切面 4 个独立接缝验证全绿）。

## [0.48.0] - 2026-10-30 - 拾遗（Shiyi · 遗漏补偿与零召回自适应补救引擎）

> 版本代号「拾遗」：出自唐代谏官官职「拾遗」（杜甫曾任左拾遗），取「捡回遗漏之物、补苴罅漏」之意——对应本版主题「遗漏补偿与零召回自适应补救引擎、分阶模糊回溯、跨辨域安全放宽与认知上下文补偿切面，ADR-0069 落地」：突破常规检索在模糊长尾表述或同义分词偏差下直接返空的局限，构建面向生产环境的高性能自适应补救引擎 `ShiyiEngine`（票 01）。支持针对模糊失配查询启动分级补救（Tier 1: 宽松结巴分词与字符级子序列模糊召回；Tier 2: 跨辨域与分轨安全放宽；Tier 3: 衰减时效回拨）；定义清晰的补救候选契约 `RemedyCandidate`，明确标注补救梯队（`remedy_tier`）与加权置信度；全链路正文与理由强制经由樊篱（`wrap_as_data`）数据围栏包裹，严密防御提示注入，并严守行级租户属主安全隔离纪律（宁漏不越权）。打通服务层 `shiyi_service.py`、HTTP REST 路由（`POST /shiyi/remedy`）、MCP 工具（`shiyi_remedy`，工具池扩充至 82 个里程碑且 1:1 双向对齐）与认知上下文 `CognitiveContext`（票 02，拾遗补救切面结构化呈现）。全量覆盖端到端不 mock 冒烟测试。票据 `.scratch/v0.48-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **核心拾遗自适应补偿引擎 ShiyiEngine（票 01，详见 `lantai/cognition/shiyi.py` 与 `tests/test_shiyi_engine_smoke.py`）**：
  - 遗漏补救候选模型：定义 `RemedyCandidate` 契约，自动纳管模糊词条、子序列重合度与补救原由，支持樊篱安全渲染；
  - 多阶自适应补救算法：实现 `remedy_recall`，支持常规零召回时的分阶回溯与辨域安全放宽计算；
  - 樊篱安全隔离与行级 ACL：补救正文强制施加 `<memory_data>` 围栏隔离，严格校验租户与用户属主（宁漏不越权）；
  - 交付端到端不 mock 冒烟测试 `tests/test_shiyi_engine_smoke.py`（零召回模糊补救、辨域安全放宽与置信度校准、樊篱包裹防注入、行级 ACL 隔离 4 个独立接缝验证全绿）。
- **拾遗服务层、REST 路由、MCP 82 工具池扩充与认知上下文切面（票 02，详见 `lantai/services/shiyi_service.py`、`lantai/api/routes_shiyi.py`、`lantai/cli/mcp.py`、`lantai/cognition/context.py` 与 `tests/test_shiyi_api_smoke.py`）**：
  - 服务层封装：新增 `remedy_recall_service`，统一管理 DB 会话生命周期与自适应补救参数调度；
  - HTTP REST 路由：新增 `POST /shiyi/remedy` 鉴权端点，挂载入 `app.py` 核心鉴权路由；
  - MCP 工具池扩容至 82 工具里程碑：注册 `shiyi_remedy` 工具并实现 `handle_shiyi_remedy` 处理器，`TOOLS` 与 `TOOL_HANDLERS` 1:1 双向一致性守卫全绿；
  - 认知上下文增强：`CognitiveContext.to_prompt()` 引入 `shiyi_digest` 切面，结构化渲染补救候选与置信度梯队（樊篱包裹）；
  - 交付端到端不 mock 冒烟测试 `tests/test_shiyi_api_smoke.py`（服务层调用、REST 端点、MCP 协议往返与 82 工具对齐、认知上下文补救切面 4 个独立接缝验证全绿）。

## [0.47.0] - 2026-10-30 - 佐证（Zuozheng · 证据链反查与可解释性溯源图谱引擎）

> 版本代号「佐证」：出自《宋刑统》「案情显明，须得佐证」，喻指辅助证据、相互勘验印证与决策依据解释——对应本版主题「证据链反查与可解释性溯源图谱引擎、断言反向印证、多跳因果溯源与认知上下文证据切面，ADR-0068 落地」：突破既有检索粗粒度前缀截取、缺乏图谱因果支撑的局限，构建面向生产环境的高性能可解释性反查引擎 `ZuozhengEngine`（票 01）。支持针对任意记忆反查因果推演边（`supports` / `causes` / `refines` 等）与关联 `Evidence` 实体构建多跳证据链（`inspect_evidence`）；支持根据自然语言断言反向召回支撑证据簇并计算综合置信度得分（`verify_claim`）；全链路证据正文与推理原因强制经由樊篱（`wrap_as_data`）数据围栏包裹，严密防御提示注入，并严守行级租户属主安全隔离纪律（宁 miss 不脏写）。打通服务层 `zuozheng_service.py`、HTTP REST 路由（`GET /zuozheng/inspect/{id}`、`POST /zuozheng/verify`）、MCP 工具（`zuozheng_evidence`，工具池扩充至 81 个里程碑且 1:1 双向对齐）与认知上下文 `CognitiveContext`（票 02，佐证支撑证据切面结构化呈现）。全量覆盖端到端不 mock 冒烟测试。票据 `.scratch/v0.47-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **核心佐证证据链拓扑与反查验证引擎 ZuozhengEngine（票 01，详见 `lantai/cognition/zuozheng.py` 与 `tests/test_zuozheng_engine_smoke.py`）**：
  - 证据链拓扑模型：定义 `EvidenceNode` 与 `ProofChain` 契约，统一纳管记忆节点、因果推演边与直接证据实体；
  - 深度证据反查：多跳因果拓扑展开（`inspect_evidence`），自动追踪前置因果与直接佐证，计算拓扑综合置信度；
  - 自然语言断言印证：基于结巴分词与置信度加权的断言反向印证算法（`verify_claim`），实现客观证据快速检验；
  - 樊篱安全隔离与行级 ACL：证据节点正文全面由樊篱（`wrap_as_data`）包裹，落实租户与属主访问收窄（宁 miss 不脏写）；
  - 交付端到端不 mock 冒烟测试 `tests/test_zuozheng_engine_smoke.py`（证据链反查展开、断言反查印证、樊篱包裹防注入、行级 ACL 隔离 4 个独立接缝验证全绿）。
- **佐证服务层、REST 路由、MCP 81 工具池扩充与认知上下文切面（票 02，详见 `lantai/services/zuozheng_service.py`、`lantai/api/routes_zuozheng.py`、`lantai/cli/mcp.py`、`lantai/cognition/context.py` 与 `tests/test_zuozheng_api_smoke.py`）**：
  - 服务层封装：新增 `inspect_evidence_service` 与 `verify_claim_service`，统一管理 DB 会话生命周期；
  - HTTP REST 路由：新增 `GET /zuozheng/inspect/{memory_id}` 与 `POST /zuozheng/verify` 鉴权端点，挂载入 `app.py` 核心鉴权路由；
  - MCP 工具池扩容至 81 工具里程碑：注册 `zuozheng_evidence` 工具并实现 `handle_zuozheng_evidence` 处理器，`TOOLS` 与 `TOOL_HANDLERS` 1:1 双向一致性守卫全绿；
  - 认知上下文增强：`CognitiveContext.to_prompt()` 引入 `zuozheng_digest` 切面，结构化渲染置信度证据节点（樊篱包裹）；
  - 交付端到端不 mock 冒烟测试 `tests/test_zuozheng_api_smoke.py`（服务层调用、REST 端点、MCP 协议往返与 81 工具对齐、认知上下文证据切面 4 个独立接缝验证全绿）。

## [0.46.0] - 2026-10-30 - 法门（Famen · 程序性技能资产与执行流水线引擎）

> 版本代号「法门」：出自《法华经》「深入法门，通达诸法」，喻指可循之法、可行之门与标准作业规程（SOP）——对应本版主题「程序性技能资产归一、触发规则动态匹配、SOP 结构化提取与认知上下文技能切面，ADR-0067 落地」：突破既有 `SkillCrystal` 技能结晶与 `MemoryItem` 步骤松散孤立、无法直接指导 Agent 动作的局限，构建面向生产环境的高性能技能流水线引擎 `FamenEngine`（票 01）。支持从结晶与程序性规则中归一化提取标准化 SOP 步骤对象契约；支持结合任务意图与触发词进行动态相关度排序与截断（`match_skills`）；全链路步骤正文与规则说明强制经由樊篱（`wrap_as_data`）数据围栏包裹，严密防御提示注入，并严守行级租户属主安全隔离与集群公共结晶共享（`shared_scope="swarm_shared"`）纪律（宁 miss 不脏写）。打通服务层 `famen_service.py`、HTTP REST 路由（`GET /famen/match`、`GET /famen/skills/{skill_id}`、`GET /famen/list`）、MCP 工具（`famen_skill`，工具池扩充至 80 个里程碑且 1:1 双向对齐）与认知上下文 `CognitiveContext`（票 02，法门 SOP 步骤切面结构化呈现）。全量覆盖端到端不 mock 冒烟测试。票据 `.scratch/v0.46-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **核心法门程序性技能与 SOP 聚合匹配引擎 FamenEngine（票 01，详见 `lantai/cognition/famen.py` 与 `tests/test_famen_engine_smoke.py`）**：
  - 多源技能资产归一化：提取并纳管 SkillCrystal 与 MemoryItem procedural 规程步骤，形成统一的 `SkillAsset` 契约；
  - 动态触发规则匹配：基于任务意图、触发规则、技能名称与步骤上下文的多维度综合打分算法（`match_skills`），实现 SOP 精准匹配；
  - 樊篱安全隔离：SOP 步骤列表、流程正文与触发词规则全面经樊篱（`wrap_as_data`）包裹，杜绝提示注入攻击；
  - 行级租户与公共共享隔离：严格校验属主与租户，支持 `swarm_shared` 集群公共结晶安全共享，越权资产完全屏蔽；
  - 交付端到端不 mock 冒烟测试 `tests/test_famen_engine_smoke.py`（多源技能聚合、任务驱动匹配排序、樊篱包裹防注入、行级 ACL 隔离 4 个独立接缝验证全绿）。
- **法门 API 路由、MCP 80 工具池扩充与认知上下文 SOP 切面（票 02，详见 `lantai/services/famen_service.py`、`lantai/api/routes_famen.py`、`lantai/cli/mcp.py`、`lantai/cognition/context.py` 与 `tests/test_famen_api_smoke.py`）**：
  - 服务层封装：新增 `match_skills_service`、`get_skill_detail_service` 与 `list_skills_service`，统一管理 DB 会话生命周期；
  - HTTP REST 路由：新增 `GET /famen/match`、`GET /famen/skills/{skill_id}`、`GET /famen/list` 鉴权端点，挂载入 `app.py` 核心鉴权路由；
  - MCP 工具池扩容至 80 工具里程碑：注册 `famen_skill` 工具并实现 `handle_famen_skill` 处理器，`TOOLS` 与 `TOOL_HANDLERS` 1:1 双向一致性守卫全绿；
  - 认知上下文增强：`CognitiveContext.to_prompt()` 引入 `famen_digest` 切面，结构化渲染适用法门与编号步骤（樊篱包裹）；
  - 交付端到端不 mock 冒烟测试 `tests/test_famen_api_smoke.py`（服务层调用、REST 端点、MCP 协议往返与 80 工具对齐、认知上下文 SOP 切面 4 个独立接缝验证全绿）。

## [0.45.0] - 2026-10-30 - 起居注（Qijuzhu · 时序记忆脉络与每日全息纪事引擎）

> 版本代号「起居注」：出自《汉书·艺文志》「古之人君，左史记言，右史记事」，逐日详录言行得失与政事变迁——对应本版主题「时序记忆脉络、多源认知事件编排与每日全息纪事切面，ADR-0066 落地」：突破既有静态粗粒度每日盘点（`digest_worker.py`）仅在清晨产生离线 Markdown、缺乏结构化时序动态切面的局限，构建面向生产环境的高性能聚合引擎 `QijuzhuEngine`（票 01）。支持统一编排与捕获异构多源认知事件流（记忆摄入、直书/程序铁律确立、卷宗里程碑流转、参商冲突仲裁与记忆演化提案应用）；提供今日与任意指定日期的全息日度纪事（`get_daily_chronicle`，支持宏观变迁摘要、量化度量指标与事件流），以及跨任意时间窗（`days`、`limit`）的滑动时间轴脉络（`get_timeline`）；所有纪事摘要、条目标题与笔记细节强制经由樊篱（`wrap_as_data`）数据围栏包裹，严防提示注入，并严守行级租户属主安全隔离与「宁 miss 不脏写」纪律。打通服务层 `qijuzhu_service.py`、HTTP REST 路由（`GET /qijuzhu/today`、`GET /qijuzhu/timeline`、`GET /qijuzhu/dates/{date_str}`）、MCP 工具（`qijuzhu_chronicle`，工具池扩充至 79 个且 1:1 双向对齐）与认知上下文 `CognitiveContext`（票 02，时序全息纪事切面置顶呈现）。全量覆盖端到端不 mock 冒烟测试。票据 `.scratch/v0.45-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **核心起居注时序记忆纪事聚合引擎 QijuzhuEngine（票 01，详见 `lantai/cognition/qijuzhu.py` 与 `tests/test_qijuzhu_engine_smoke.py`）**：
  - 多源异构认知事件流聚合：编排抓取 MemoryItem（普通摄入、程序铁律确立）、Dossier（战略目标卷宗推进与里程碑流转）、MemoryProposal（记忆演化合并、精炼与归档提案应用）时序流水；
  - 全息日度纪事与滑动时间轴脉络：`get_daily_chronicle` 产出宏观变迁叙事、5 项量化度量指标（总事件数/新记忆数/确立规则数/卷宗推进数/提案生效数）与明细事件流；`get_timeline` 产出任意多日倒序滑动时间轴脉络流并支持 limit 截断；
  - 樊篱安全隔离：所有纪事事件明细、标题与说明经由樊篱（`wrap_as_data`）数据围栏包裹，严密防御提示注入风险；
  - 严格租户与属主隔离：`_check_access` 结合 `tenant_id`、`user_id` 与 `allowed_lanes` 泳道白名单过滤，宁 miss 不脏写，越权事件零泄漏；
  - 交付端到端不 mock 冒烟测试 `tests/test_qijuzhu_engine_smoke.py`（当日全息纪事聚合、滑动时间轴排序与截断、樊篱包裹防注入、租户与泳道权限隔离 4 个独立接缝验证全绿）。
- **起居注 API 路由、MCP 工具集成与认知上下文切面（票 02，详见 `lantai/services/qijuzhu_service.py`、`lantai/api/routes_qijuzhu.py`、`lantai/cli/mcp.py`、`lantai/cognition/context.py` 与 `tests/test_qijuzhu_api_smoke.py`）**：
  - 服务层封装：新增 `get_daily_chronicle_service` 与 `get_timeline_service`，纳管 DB 会话事务生命周期；
  - HTTP REST 路由：新增 `GET /qijuzhu/today`、`GET /qijuzhu/timeline`、`GET /qijuzhu/dates/{date_str}` 鉴权端点，挂载入 `app.py` 核心路由池；
  - MCP 工具池升级：注册 `qijuzhu_chronicle` 工具并实现 `handle_qijuzhu_chronicle` 处理器，系统 MCP 工具总数扩充至 79 个，TOOLS 与 TOOL_HANDLERS 1:1 双向一致性守卫全绿；
  - 认知上下文增强：`CognitiveContext.to_prompt()` 引入 `qijuzhu_digest` 切面，置顶呈现樊篱包裹的近期起居注纪事；
  - 交付端到端不 mock 冒烟测试 `tests/test_qijuzhu_api_smoke.py`（服务层调用、REST 路由端点、MCP 协议往返与 79 工具对齐、认知上下文时序切面 4 个独立接缝验证全绿）。

## [0.44.0] - 2026-10-30 - 目次（Muci · 多维记忆知识目录与分级语义导航引擎）

> 版本代号「目次」：出自南朝梁·刘勰《文心雕龙·序志》「成岁既渐，目次来构」；部目次序、条贯井然，使开卷者一览无遗——对应本版主题「多维记忆知识目录、分级语义导航与认知上下文导览切面，ADR-0065 落地」：突破既有统计概览（`ops/overview.py`）仅有粗粒度行级计数的局限，构建面向全局记忆知识感知的高性能聚合引擎 `MuciEngine`（票 01）。支持按辨域（domain: user / session / agent）与分轨（lane: fact / rule / preference / experience / general）构建结构化知识板块，统计板块活跃度、高频标签云与最新事件时间；提供 compact（紧凑低 Token 大纲）与 full（精选条目展开）双模式，正文统一经由樊篱（`wrap_as_data`）数据围栏包裹，严密防御提示注入，并严格落实行级租户安全隔离与「宁 miss 不脏写」纪律。打通服务层 `muci_service.py`、HTTP REST 路由（`GET /catalog/overview`、`GET /catalog/domains/{domain}`）、MCP 工具（`muci_catalog`，工具池扩充至 78 个且 1:1 双向一致）与认知上下文 `CognitiveContext`（票 02，目次导览切面置顶呈现）。全量覆盖端到端不 mock 冒烟测试。票据 `.scratch/v0.44-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **核心目次多维知识目录聚合引擎 MuciEngine（票 01，详见 `lantai/cognition/muci.py` 与 `tests/test_muci_engine_smoke.py`）**：
  - 多维分级知识编目：按 `(domain, lane)` 组织为结构化 Sections，量化活跃条目与总数，提炼高频实体标签与时序晚近点；
  - 双模式大纲塑形：`compact` 模式输出极低 Token 开销的大纲导航树，`full` 模式按重要度与时效展开精选代表条目；
  - 樊篱安全隔离：精选条目正文强制经由樊篱（`wrap_as_data`）数据围栏包裹并带 `id` 属性，杜绝提示注入风险；
  - 严格租户隔离：`_check_access` 租户与属主安全校验，跨租户跨用户数据零泄漏（宁 miss 不脏写）；
  - 交付端到端不 mock 冒烟测试 `tests/test_muci_engine_smoke.py`（4 个独立接缝验证）。
- **目次 API 路由、MCP 工具集成与认知上下文导览切面（票 02，详见 `lantai/services/muci_service.py`、`lantai/api/routes_catalog.py`、`lantai/cli/mcp.py`、`lantai/cognition/context.py` 与 `tests/test_muci_api_smoke.py`）**：
  - 服务层下沉：新增 `get_catalog_service`，处理 Session 事务边界并提供统一的目录查询服务；
  - HTTP REST 路由：新增 `GET /catalog/overview` 与 `GET /catalog/domains/{domain}` 端点，挂载至 `app.py` 鉴权路由组；
  - MCP 工具池升级：注册 `muci_catalog` 工具，系统 MCP 工具总数扩充至 78 个，广告与分发字典 1:1 双向一致（`tests/test_mcp.py` 计数与 parity 守卫全绿）；
  - 认知上下文增强：`CognitiveContext.to_prompt()` 支持 `catalog_digest` 目次导览切面，大纲经樊篱包裹并置顶呈现；
  - 交付端到端不 mock 冒烟测试 `tests/test_muci_api_smoke.py`（服务层、REST 端点、MCP 协议往返与认知上下文 4 个独立接缝验证）。

## [0.43.0] - 2026-10-30 - 直书（Zhishu · 确定性高频句型直写引擎与多轨规则矩阵）

> 版本代号「直书」：出自《左传·成公七年》「史氏秉笔直书，不隐不讳」与《左传·襄公二十五年》董狐秉笔直书之典——对应本版主题「高频确定性事实零 Token 秒级直写、程序铁律硬性纳管与多轨语义分流，ADR-0064 落地」：突破既有 `lantai/parsing/fastpath.py` 仅 3 组初等正则且结构扁平的局限，构建面向生产环境的高精确定性规则引擎 `ZhishuEngine`（票 01）。支持 Fact（身份自称/职业/居所）、Preference（喜好/厌恶/习惯）、Constraint/Procedural（绝对铁律禁令/务必义务，强制 `lane="rule"` 且 `decay_class="procedural"` 永不衰减）、Config（系统配置/参数赋值结构化键值对）、General（显式备忘记录）五大核心分轨；全轨落实「宁 miss 不脏写」纪律，严苛过滤问句句尾（`?`、`吗`、`呢`等）与复合转折干扰词（`虽然`、`但是`、`如果`、`有人说`等），低置信或复杂语境零误判、安全回退大模型提取；原 `fastpath.py` 演化为 100% 向后兼容门面，既有历史测试逐字不改平滑通过。打通服务层 `zhishu_service.py`、HTTP REST 调试端点（`POST /parsing/zhishu/match`，带 `ZhishuMatchReq` 模式校验）、MCP 工具（`zhishu_check`，工具池扩充至 77 个且 1:1 双向一致）（票 02）。全量覆盖端到端不 mock 冒烟测试。票据 `.scratch/v0.43-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **核心直书确定性规则直写引擎 ZhishuEngine（票 01，详见 `lantai/parsing/zhishu.py`、`lantai/parsing/fastpath.py` 与 `tests/test_zhishu_engine_smoke.py`）**：
  - 五大分轨确定性抽取：Fact（自称/职业/居所）、Preference（喜好/偏爱/习惯）、Constraint（严禁/禁止/务必/切勿/必须）、Config（参数配置/键值映射）、General（显式备忘）；
  - 程序性约束永不衰减：Constraint 轨道提取直接标记 `decay_class="procedural"` 与 `lane="rule"`，确保安全守则与操作铁律免受 Ebbinghaus 遗忘周期降权；
  - 宁 miss 不脏写守卫（Negative Guard）：问句结尾与复合转折从句快速熔断拒判，精度指标 ≥95%；
  - 100% 向后兼容门面：`lantai/parsing/fastpath.py` 无缝转调 `ZhishuEngine`，历史下游 `memory_service` 与 `dialogue.py` 零适配开销；
  - 交付端到端不 mock 冒烟测试 `tests/test_zhishu_engine_smoke.py`（7 个独立测试用例验证）。
- **直书 API、MCP 工具集成与规则直写诊断端点（票 02，详见 `lantai/services/zhishu_service.py`、`lantai/api/routes_zhishu.py`、`lantai/models/schemas.py`、`lantai/cli/mcp.py` 与 `tests/test_zhishu_api_smoke.py`）**：
  - 服务层下沉：新增 `match_zhishu_service`，返回统一格式的规则匹配诊断契约 `{"matched": bool, "text": str, "rule": dict | None}`；
  - HTTP REST 路由：新增 `POST /parsing/zhishu/match` 诊断路由，挂载至 `app.py` 鉴权路由组；
  - MCP 工具池升级：注册 `zhishu_check` 工具，系统 MCP 工具总数扩充至 77 个，广告与分发字典 1:1 双向一致（`tests/test_mcp.py` 计数与 parity 守卫全绿）；
  - 交付端到端不 mock 冒烟测试 `tests/test_zhishu_api_smoke.py`（服务层、REST 端点、MCP 协议往返与 77 工具池对齐 4 个独立接缝验证）。

## [0.42.0] - 2026-10-30 - 卷宗（Juanzong · 长程复杂任务场景聚合与目标上下文锚定）

> 版本代号「卷宗」：出自唐代白居易《新乐府》「卷宗堆案墨翻盆」——对应本版主题「长程多轮复杂工程任务情境聚合、战略目标与阶段里程碑状态追踪、动态记忆情境聚焦与樊篱安全防御，ADR-0063 落地」：突破既有 ADR-0012 纯弱聚类 `MemoryScene` 的无状态局限，构建面向复杂多轮长程任务（如系统重构、排障演练、技术攻坚）的「情境卷宗实体（Dossier）」、「任务里程碑追踪（Milestones）」与「动态情境记忆聚焦（Dossier Context Focusing）」。核心认知引擎 `JuanzongEngine`（票 01）支持卷宗完整生命周期 CRUD、任务里程碑状态推进（`pending`/`in_progress`/`done`）与笔记追踪、记忆项保序幂等绑定/解绑、结案纪要沉淀，并基于中文 2-gram 与整词重叠优化情境聚焦检索；打通服务层 `dossier_service.py`、HTTP REST 端点（`POST /dossiers`、`GET /dossiers`、`POST /dossiers/{id}/bind`、`POST /dossiers/{id}/milestones`、`POST /dossiers/{id}/focus` 等）、MCP 工具（`dossier_create` 与 `dossier_focus`，工具池扩充至 76 个且 1:1 双向一致）与认知上下文 `CognitiveContext`（票 02，卷宗切面置顶呈现，案名、目标愿景、里程碑笔记与记忆内容全部强制经过樊篱 `<memory_data>` 防御包裹）。全链路严格实施行级租户安全隔离与「宁 miss 不脏写」纪律。票据 `.scratch/v0.42-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **核心长程任务情境卷宗聚合引擎 JuanzongEngine（票 01，详见 `lantai/cognition/juanzong.py`、`lantai/models/tables.py` 与 `tests/test_juanzong_engine_smoke.py`）**：
  - 数据模型实体：新增 `Dossier` 模型表，涵盖 `id`, `tenant_id`, `user_id`, `title`, `goal`, `status`, `milestones`, `memory_ids`, `metadata_json` 与时间戳；
  - 卷宗生命周期管理：`create_dossier`、`get_dossier`、`list_dossiers`、`close_dossier` 完整覆盖创建、查询、办结与归档；
  - 里程碑状态机：`update_milestone` 推进任务阶段并记录进展笔记，支持 SQLAlchemy JSON 字段变更感知（`flag_modified`）；
  - 记忆项保序绑定：`bind_memories` 与 `unbind_memories` 幂等维护案卷关联事实；
  - 情境记忆聚焦：`focus_dossier_context` 定向召回卷宗绑定事实并结合中文分词排重与置信度打分置顶；
  - 严格租户隔离：`_check_access` 双重属性（`tenant_id` + `user_id`）行级安全校验，跨租户跨用户数据零泄漏（宁 miss 不脏写）；
  - 交付端到端不 mock 冒烟测试 `tests/test_juanzong_engine_smoke.py`（5 个独立接缝验证）。
- **卷宗 API、MCP 工具集成与认知上下文情境聚焦切面（票 02，详见 `lantai/services/dossier_service.py`、`lantai/api/routes_dossiers.py`、`lantai/cli/mcp.py`、`lantai/cognition/context.py` 与 `tests/test_juanzong_api_smoke.py`）**：
  - 服务层下沉：封装 `create_dossier_service`、`get_dossier_service`、`list_dossiers_service`、`bind_memories_service`、`unbind_memories_service`、`update_milestone_service`、`focus_dossier_service` 与 `close_dossier_service`，带调用方 principal 行级鉴权；
  - 认知上下文增强：`CognitiveContext.to_prompt()` 支持 `active_dossier` 聚焦切面，案名、战略目标、里程碑状态与聚焦记忆全部经樊篱（`wrap_as_data`）数据围栏包裹安全渲染；
  - HTTP REST 路由：新增 `/dossiers` 系列 REST 端点，挂载至 `app.py` 鉴权路由组；
  - MCP 工具池升级：注册 `dossier_create` 与 `dossier_focus`，工具总数扩充至 76 个，广告与分发字典 1:1 双向一致（`tests/test_mcp.py` 计数与 parity 守卫全绿）；
  - 交付端到端不 mock 冒烟测试 `tests/test_juanzong_api_smoke.py`（HTTP、MCP、CognitiveContext、服务层 5 个独立接缝验证）。

## [0.41.0] - 2026-10-30 - 参商（Canshang · 深度矛盾冲突检测与多跳信念一致性仲裁）

> 版本代号「参商」：出自《左传·昭公元年》「虽相背而不同出，如参商之不可见也」——对应本版主题「深层矛盾冲突检测、多跳因果一致性审计与更漏时间轴深度多方仲裁直断，ADR-0062 落地」：突破既有 v0.3 静态孤立双记忆相似度启发式打分局限，构建面向全局信念与因果网络的深层冲突检测与一致性仲裁引擎 `CanshangEngine`（票 01）。支持基于语义相斥与结构化实体属性的显式抵触推导、跨 `causes`/`leads_to` 因果链的多跳隐性矛盾推导（Multi-hop Causal Contradiction），以及全域/实体信念一致性审计矩阵扫描（Consistency Audit Matrix）；针对多方互斥主张，提供结合更漏双时间轴（`event_time` 时效优势）、证据链完整度与置信度量化的深度裁决直断（Deep Arbitration）。打通服务层 `audit_consistency_service`/`arbitrate_deep_service`、HTTP REST 端点（`POST /conflicts/scan-consistency`、`POST /conflicts/arbitrate-deep`）、MCP 工具（`conflict_scan_consistency`、`conflict_deep_arbitrate`，工具池扩充至 74 个且 1:1 双向一致）与认知上下文 `CognitiveContext`（票 02，参商冲突违例经樊篱 `wrap_as_data` 数据围栏包裹安全渲染）。全链路严格实施行级租户安全隔离与「宁 miss 不脏写」纪律。票据 `.scratch/v0.41-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **核心深层冲突检测与信念一致性仲裁引擎 CanshangEngine（票 01，详见 `lantai/cognition/canshang.py` 与 `tests/test_canshang_engine_smoke.py`）**：
  - 两两主张与属性互斥冲突检测：`detect_pairwise_contradiction` 支持结构化属性（`value`/`role`/`state`/`policy`）与文本相斥语义对规则推导；
  - 多跳因果隐性相悖推导：`audit_consistency` 沿因果边（`MemoryEdge: causes/leads_to`）自动追踪前置诱因与推演结论相抵触的多跳链条；
  - 信念一致性审计矩阵：支持全域范围或指定实体名称的一致性扫描，生成结构化违例审计报告 `ConsistencyAuditReport`；
  - 深度多方仲裁直断：`arbitrate_deep_conflict` 对多方互斥记忆进行综合权衡，优先采纳更漏事件轴晚近且置信度高的数据，给出裁决策略与决策轨迹；
  - 严格租户隔离：`_check_access` 行级租户安全校验，跨租户跨用户数据零泄漏（宁 miss 不脏写）；
  - 交付端到端不 mock 冒烟测试 `tests/test_canshang_engine_smoke.py`（5 个独立接缝验证）。
- **参商一致性审计 API、MCP 工具集成与认知上下文抵触防御（票 02，详见 `lantai/services/conflict_service.py`、`lantai/api/routes_conflicts.py`、`lantai/cli/mcp.py`、`lantai/cognition/context.py` 与 `tests/test_canshang_api_smoke.py`）**：
  - 服务层下沉：封装 `audit_consistency_service` 与 `arbitrate_deep_service`，带调用方 principal 行级鉴权；
  - 认知上下文增强：`CognitiveContext.to_prompt()` 支持参商违例结构专化渲染，全部正文经樊篱（`wrap_as_data`）数据围栏包裹；
  - HTTP REST 路由：新增 `POST /conflicts/scan-consistency` 与 `POST /conflicts/arbitrate-deep`，并在 `schemas.py` 增加请求约束模型；
  - MCP 工具池升级：注册 `conflict_scan_consistency` 与 `conflict_deep_arbitrate`，工具总数扩充至 74 个，广告与分发字典 1:1 双向一致（`tests/test_mcp.py` 计数与 parity 守卫全绿）；
  - 交付端到端不 mock 冒烟测试 `tests/test_canshang_api_smoke.py`（HTTP、MCP、CognitiveContext 3 个独立接缝验证）。

## [0.40.0] - 2026-10-30 - 格物（Gewu · 反事实推理与前瞻因果推演引擎）

> 版本代号「格物」：出自《礼记·大学》「致知在格物，物格而后知至」——对应本版主题「因果反事实推理、前瞻决策影响推演与关键因果路径量化，ADR-0061 延伸落地」：突破既有 v0.35 仅能进行事后回溯（Retrospective Provenance）的局限，提供面向 Agent 决策前瞻推演的「反事实根因判定（Counterfactual Necessity Evaluation）」、「前瞻次生决策影响面模拟（Projected Causal Impact）」与「主导因果强路径量化（Critical Causal Path）」。核心引擎 `CausalEngine`（票 01）支持 Do-calculus 介入阻断单点诱因并探索存活替代路径，计算必要性 `is_necessary`，正向模拟次生波及半径与累积置信度衰减，内置闭环防死锁熔断；打通服务层 `evaluate_counterfactual_service`/`project_causal_impact_service`、HTTP REST 端点（`POST /edges/counterfactual`、`POST /edges/project-impact`）、MCP 工具（`causal_counterfactual`、`causal_project_impact`，工具池扩充至 72 个且 1:1 双向一致）与认知上下文 `CognitiveContext`（票 02，反事实替代路径经樊篱 `wrap_as_data` 数据围栏包裹安全渲染）。全链路执行行级租户隔离（`tenant_id` + `user_id`），不可见节点无法穿透或推演。票据 `.scratch/v0.40-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **核心反事实与因果推演认知引擎升级 CausalEngine（票 01，详见 `lantai/cognition/causal.py` 与 `tests/test_gewu_counterfactual_engine_smoke.py`）**：
  - 反事实因果判定：`evaluate_counterfactual` 支持介入阻断目标节点的指定诱因，逆向分析存活替代路径，计算必要性 `is_necessary` 与 `reachable_without_node`；
  - 前瞻影响面推演：`project_causal_impact` 沿 `causes`/`leads_to` 前瞻推演次生波及面与累积置信度衰减，内置环路与死循环熔断保护；
  - 关键因果路径量化：`find_critical_causal_path` 在 DAG 因果拓扑中量化乘积置信度最高的主导因果链；
  - 严格租户隔离：`_check_node_access` 行级租户隔离前置校验，非本人数据不可见、不可穿透（宁 miss 不脏写）；
  - 交付端到端不 mock 冒烟测试 `tests/test_gewu_counterfactual_engine_smoke.py`（5 个独立接缝验证）。
- **格物反事实 API、MCP 工具集成与认知上下文推演切面（票 02，详见 `lantai/services/provenance_service.py`、`lantai/api/routes_edges.py`、`lantai/cli/mcp.py`、`lantai/cognition/context.py` 与 `tests/test_gewu_counterfactual_api_smoke.py`）**：
  - 服务层下沉：封装 `evaluate_counterfactual_service` 与 `project_causal_impact_service`，带调用方 principal 行级鉴权；
  - 认知上下文增强：`CognitiveContext.to_prompt()` 支持反事实替代因果路径渲染，全部正文经樊篱（`wrap_as_data`）数据围栏包裹；
  - HTTP REST 路由：新增 `POST /edges/counterfactual` 与 `POST /edges/project-impact`；
  - MCP 工具池升级：注册 `causal_counterfactual` 与 `causal_project_impact`，工具总数扩充至 72 个，广告与分发字典 1:1 双向一致（`tests/test_mcp.py` 计数与 parity 守卫全绿）；
  - 交付端到端不 mock 冒烟测试 `tests/test_gewu_counterfactual_api_smoke.py`（4 个独立接缝验证）。

## [0.39.0] - 2026-10-30 - 润物（Runwu · 自适应认知中间件与无感上下文装载）

> 版本代号「润物」：出自唐代杜甫《春夜喜雨》「随风潜入夜，润物细无声」——对应本版主题「核心认知自适应装载引擎 RunwuEngine、认知切面紧凑萃取与 ASGI / MCP / HTTP 全链路自适应护航」：打破 Agent 每次都需要显式主动调用复杂检索工具才能获取指导规则与历史教训的局限，构建自适应认知装载引擎 `RunwuEngine`（票 01），实现无感护航与零网络阻塞的轻量抽取；打通 `GET /cognitive/summary` REST 端点、MCP 工具 `cognitive_summary`（工具池扩充至 70 个且 1:1 双向一致）与 `CognitiveMiddleware` ASGI 中间件自适应注入（票 02，自动解析请求任务并在响应头 `X-Cognitive-Context` 注入 Base64 编码紧凑认知摘要）。安全防护：全流程通过「樊篱」（`wrap_as_data`）数据围栏包裹（OWASP LLM01 防护），预算安全截断并自动补齐闭合标签；支持 `tenant_id` + `user_id` 双重收窄，跨租户 0 泄漏，非 admin 严格归属隔离。票据 `.scratch/v0.39-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **核心认知自适应装载引擎 RunwuEngine（票 01，详见 `lantai/runtime/runwu.py` 与 `tests/test_runwu_engine_smoke.py`）**：
  - 核心装载引擎：提供 `RunwuEngine` 与 `RunwuSummary` 数据载体，支持规则（`rules`）、历史教训（`failures`）与主动求证探针（`probes`，联动探颐 `TanyiEngine`）三维紧凑萃取与格式化；
  - 纯内存与零外部阻塞：纯 SQL 检索与词面相关度排序，无大模型或外部网络阻塞风险（宁 miss 不脏写）；
  - 字符预算安全控制：支持 `char_budget`（默认 350 字以内）预算控制与文本截断，自动校验并补齐樊篱闭合标签（`</memory_data>`），杜绝注入逃逸风险；
  - 严格租户隔离：支持 `tenant_id` + `user_id` 双重隔离，跨租户跨用户数据零泄漏；
  - 门面兼容（ADR-0001）：`lantai/runtime/middleware.py` 的 `build_cognitive_summary` 代理至 `RunwuEngine`，在 `lantai/runtime/__init__.py` 导出相关组件；
  - 交付端到端不 mock 冒烟测试 `tests/test_runwu_engine_smoke.py`（4 个独立接缝验证）。
- **润物 API、MCP 工具集成与 ASGI 自适应注入中间件（票 02，详见 `lantai/api/routes_cognitive.py`、`lantai/cli/mcp.py`、`lantai/runtime/middleware.py` 与 `tests/test_runwu_api_smoke.py`）**：
  - HTTP REST 路由：升级 `GET /cognitive/summary`，支持任务提取并导出结构化认知（rules / failures / probes / summary 紧凑文本 / char_count），保持 v0.4 字段向后兼容；
  - MCP 工具池升级：注册 `cognitive_summary`，工具总数扩充至 70 个，广告与分发字典 1:1 双向一致（`tests/test_mcp.py` 计数与 parity 守卫同步更新）；
  - ASGI 自适应中间件升级：`CognitiveMiddleware` 支持从 `X-Task` 请求头自适应提取任务上下文（支持 URL 解码），并在响应头自动注入 Base64 编码认知摘要 `X-Cognitive-Context`；
  - 交付端到端不 mock 冒烟测试 `tests/test_runwu_api_smoke.py`（HTTP、MCP、ASGI 3 个独立接缝）。

## [0.38.0] - 2026-10-30 - 探颐（Tanyi · 存储驱动主动探针与自然交互消歧）

> 版本代号「探颐」：《易·系辞上》「探赜索隐，钩深致远」——对应本版主题「存储驱动主动探针巡检与归属安全收窄（ADR-0037 延伸落地）」：v0.34 的探颐插桩引擎（`TanyiProbingEngine`）依赖检索召回命中才生成探针，未命中的未决冲突与模糊事实长期沉没。本版新增存储驱动主动巡检引擎 `TanyiEngine`（票 01），直接扫描归属范围内未决冲突（`ConflictEvent` open）与低置信度模糊事实（`confidence < 0.6`，最模糊优先），输出结构化探针候选 `TanyiProbeItem` 并生成自然对话追问 `generate_clarification_prompts`（冲突探针同时呈现新旧主张，便于用户裁决）；打通 `POST /cognition/tanyi/probe` REST 端点、MCP 工具 `memory_tanyi_probe`（工具池扩充至 69 个且广告/分发 1:1 双向一致）与 `CognitiveContext.clarification_probes` 认知上下文切面（票 02，樊篱围栏专化渲染）。归属安全：非 admin 按 `tenant_id` + `user_id` 双重过滤，同租户跨属主零泄漏，NULL 属主老行不可见——探针把正文渲染成问句直达用户，是比列表更直接的泄漏面，贯彻「宁 miss 不脏写」。票据 `.scratch/v0.38-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **探颐存储驱动主动探针引擎（票 01，详见 `lantai/cognition/tanyi.py` 与 `tests/test_tanyi_engine_smoke.py`）**：
  - 核心引擎设计：在既有召回驱动的 `TanyiProbingEngine`（v0.34 ADR-0037 插桩）之外新增存储驱动主动巡检引擎 `TanyiEngine`，无需等待检索命中即可扫描归属范围内未决冲突（`ConflictEvent` open）与低置信度模糊事实（`confidence < 0.6`，最模糊优先）；
  - 探针候选与追问生成：输出 `TanyiProbeItem` 结构化探针列表，`generate_clarification_prompts` 将冲突/模糊事实转换为面向用户自然对话的轻量追问（冲突探针同时呈现新旧双方主张，便于用户裁决）；
  - 归属过滤：`tenant_id`/`user_id` 双重收窄（票 02 扩展），严格跨租户 100% 隔离，贯彻「宁 miss 不脏写」；
  - 交付端到端不 mock 冒烟测试 `tests/test_tanyi_engine_smoke.py`（真实 in-memory SQLite，4 接缝）。
- **探颐消歧 API、MCP 工具集成与认知上下文求证切面（票 02，详见 `lantai/api/routes_tanyi.py`、`lantai/cognition/context.py`、`lantai/cli/mcp.py` 与 `tests/test_tanyi_api_smoke.py`）**：
  - HTTP REST 路由：新增 `POST /cognition/tanyi/probe`，非 admin 按 `tenant_id` + `user_id` 双重过滤（同租户跨属主零泄漏，NULL 属主老行不可见），admin 全权；
  - 认知上下文切面扩展：在 `CognitiveContext` 新增 `clarification_probes` 切面并专化渲染（只出问题文本，不 repr 结构字典），樊篱数据围栏全流程防护；
  - MCP 工具池升级：注册 `memory_tanyi_probe`，工具总数扩充至 69 个，广告与分发 1:1 双向一致（`tests/test_mcp.py` 计数与 parity 守卫同步更新）；
  - 交付端到端不 mock 冒烟测试 `tests/test_tanyi_api_smoke.py`（TestClient + MCP 标准分发三接缝）。

## [0.37.0] - 2026-10-29 - 三省（Sanxing · 认知层记忆反思蒸馏与自我审视闭环）

> 版本代号「三省」：《论语·学而》「吾日三省吾身」——认知层反思自省与记忆健康审视，对应本版主题「认知层反思自省引擎、记忆健康审视与三省自检闭环，ADR-0013 / ADR-0044 落地」：日常交互长期积累下，存量事实与规则常面临被取代残留、时效过期、未决冲突或低频碎片等隐性质量隐患。构建认知层自我审视回环引擎 `SanxingEngine`，支持对记忆库进行多维健康扫描（`evaluate_health`），涵盖规则 R1（被取代残留：`superseded_by` 但仍为 active）、规则 R2（时效过期：`valid_to` 过期且非 `procedural` 铁律）、规则 R3（未决冲突：`ConflictEvent` 状态仍为 open）、规则 R4（低帮助度碎片：`use_count >= 5` 且 `helpful_count == 0`）；计算健康评分并输出结构化快照 `SanxingHealthSnapshot` 与自省审计建议报告 `SanxingAuditResult`；暴露 RESTful 端点 `GET /evolution/sanxing/health` 与 `POST /evolution/sanxing/audit`，以及 MCP 协议标准化工具 `memory_health_inspect`、`memory_audit_report`（MCP 工具池扩充至 68 个且 1:1 双向一致）；在 `CognitiveContext` 中新增 `reflection_audit` 切面并渲染结构化 Markdown Prompt，严守「樊篱」数据围栏与行级 ACL 租户隔离防火墙。登记见 [ADR-0013](docs/adr/0013-naming-system.md) §7 与 `CONTEXT.md` 词汇表。票据 `.scratch/v0.37-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **认知层反思自省引擎与记忆健康审视（票 01，详见 `lantai/cognition/sanxing.py`、`lantai/cognition/__init__.py` 与 `tests/test_sanxing_engine_smoke.py`）**：
  - 核心引擎设计：落地 `SanxingEngine`，提供多维健康缺陷主动扫描 `evaluate_health` 与报告聚合 `generate_audit_report`；
  - 多维自检规则 R1-R4：覆盖被取代残留、时效过期、未决冲突与低帮助度碎片，程序性铁律（`procedural`）天然豁免；
  - 租户行级安全与快捷入口：严格受限 `_owned_memory_scope` 与 `_visible_conflict_ids` 隔离，导出快捷函数 `inspect_memory_health`；
  - 交付端到端不 mock 冒烟测试 `tests/test_sanxing_engine_smoke.py`。
- **三省自检 API、MCP 工具集成与认知上下文切面（票 02，详见 `lantai/cognition/context.py`、`lantai/api/routes_evolution.py`、`lantai/cli/mcp.py` 与 `tests/test_sanxing_api_smoke.py`）**：
  - 认知上下文切面扩展：在 `CognitiveContext` 扩展 `reflection_audit` 切面并渲染结构化 Markdown Prompt，樊篱数据围栏全流程防护；
  - HTTP REST 路由：新增 `GET /evolution/sanxing/health` 与 `POST /evolution/sanxing/audit`，租户越权隔离严密把守；
  - MCP 工具池升级：注册 `memory_health_inspect` 与 `memory_audit_report`，工具总数扩充至 68 个，广告与分发 1:1 双向一致；
  - 交付端到端不 mock 冒烟测试 `tests/test_sanxing_api_smoke.py` 与 `tests/test_mcp.py` 回归。

## [0.36.0] - 2026-10-29 - 尘封（Chenfeng · 冷热分级存储与记忆分层归档收纳）

> 版本代号「尘封」：典籍意象「积尘封藏，不弃不取；考订功过，去芜存菁」——对应本版主题「冷热分级存储与自动封藏策略引擎、尘封档案专用检索与穿透回溯，ADR-0013 / ADR-0005 / 白皮书路线图 P0-2 落地」：针对日常交互中低活跃度、衰减分触底（`decay_score < 0.05`）或长期未命中的历史记忆常驻热层带来的向量/全文索引稀释开销，构建认知层冷热分级管理引擎 `ChenfengEngine`。支持艾宾浩斯时效衰减率、长期未访问时限（>30天且0使用）与生命周期状态显式退役（`RETIRED`）三维冷数据检测（`evaluate_cold_candidates`），实现程序性铁律（`procedural`）天然豁免与跨租户越权防护；提供批量自动封藏机制（`archive_cold_memories`），在原子变更 `active -> archived` 的同时自动落盘 `MemoryCheckpoint`（`trigger='chenfeng_archive'`）快照底本保障秒级可逆与审计追溯；标准热检索（`hybrid_search`）默认隔离尘封数据；提供专用尘封检索通道（`search_archived` 与 `search_archived_memories`）、解封恢复（`unarchive_memory` 与 `unarchive_memory_entry`，解封严格核验并补齐 FTS 与向量两层索引在场性）、REST 端点（`GET /memories/archived` 与 `POST /memories/{memory_id}/unarchive`）与 MCP 工具（`memory_archived_search`、`memory_unarchive`，工具总数扩充至 66 个）；在认知上下文 `CognitiveContext` 中新增 `archived_history` 历史溯源切面及结构化 Markdown Prompt 渲染。登记见 [ADR-0013](docs/adr/0013-naming-system.md) §7 与 `CONTEXT.md` 词汇表。票据 `.scratch/v0.36-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **冷热分级存储与自动封藏策略引擎（票 01，详见 `lantai/cognition/chenfeng.py`、`lantai/cognition/__init__.py` 与 `tests/test_chenfeng_tier_smoke.py`）**：
  - 认知层策略引擎：落地 `ChenfengEngine`，提供多维冷数据识别扫描 `evaluate_cold_candidates`；
  - 多维冷判别准则：涵盖艾宾浩斯时效衰减阈值（`decay_score < 0.05`）、长期闲置时限（`idle_days > 30` 且 `use_count == 0`）与显式退役状态（`lifecycle_status == RETIRED`），铁律（`procedural`）天然豁免；
  - 批量封藏与快照保障：实现 `archive_cold_memories`，推进状态转入 `archived`，自动记录 `MemoryCheckpoint` 底本快照及 `audit_event` 审计日志，确保秒级可逆；
  - 检索隔离与租户安全：热检索 `hybrid_search` 严格过滤排除尘封数据，严守 `_owned_scope` 租户属主防火墙；
  - 交付端到端不 mock 冒烟测试 `tests/test_chenfeng_tier_smoke.py`。
- **尘封专栏 API、MCP 工具集成与归档穿透追溯（票 02，详见 `lantai/cognition/chenfeng.py`、`lantai/cognition/context.py`、`lantai/api/routes_memory.py`、`lantai/cli/mcp.py` 与 `tests/test_chenfeng_api_smoke.py`）**：
  - 专用归档检索与解封接口：实现 `ChenfengEngine.search_archived` 与 `ChenfengEngine.unarchive_memory`，支持关键词、时间范围、分轨与场景过滤，解封恢复双层在场性（FTS 与向量索引补齐）；
  - 认知上下文切面：在 `CognitiveContext` 扩展 `archived_history` 字段与 Markdown Prompt 结构化渲染；
  - HTTP REST 路由：新增 `GET /memories/archived` 与 `POST /memories/{memory_id}/unarchive`，跨租户越权操作 403 严格阻断；
  - MCP 工具池升级：注册 `memory_archived_search` 与 `memory_unarchive`，工具总数扩充至 66 个并满足 1:1 分发一致性；
  - 交付端到端不 mock 冒烟测试 `tests/test_chenfeng_api_smoke.py`。

## [0.35.0] - 2026-10-29 - 格物（Gewu · 因果时序拓扑与事件溯源链）

> 版本代号「格物」：《礼记·大学》「致知在格物，物格而后知至」——穷究事物之原理与因果脉络，对应本版主题「格物（Gewu · 因果时序拓扑与事件溯源链，ADR-0013 / ADR-0037 / docs/future-roadmap.md 方向二落地）」：突破单点静态事实记忆的局限，构建面向 Agent 决策分析的因果拓扑推演、前置根因溯源（root cause analysis）与时序演进叙事能力。基于关系模型（`MemoryEdge`）建立因果关系拓扑（`causes`、`leads_to`、`triggered_by`），落地认知层因果回溯引擎 `CausalEngine` 与单步实体 `CausalHop`，支持正向次生链探索、逆向根因探测与有向图环路熔断保护；对外暴露高阶决策归因接口 `attribute_decision`、端点 `GET /edges/provenance`、`GET /edges/timeline` 与 MCP 协议工具（`causal_trace`、`event_timeline`），并在认知上下文 `CognitiveContext` 中拓展 `causal_provenance` 决策脉络切面，支持渲染结构化 Markdown Prompt 注入智能体决策；全程严守行级 ACL 租户防火墙，保障私密因果链路跨租户隔离。登记见 [ADR-0013](docs/adr/0013-naming-system.md) §7 与 `CONTEXT.md` 词汇表。票据 `.scratch/v0.35-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **格物因果时序拓扑与因果回溯引擎（票 01，详见 `lantai/cognition/causal.py`、`lantai/cognition/__init__.py` 与 `tests/test_gewu_causal_topology_smoke.py`、`tests/test_gewu_causal_engine_smoke.py`）**：
  - 核心引擎设计：落地 `CausalEngine` 与 `CausalHop`，依托 `MemoryEdge` 因果三元组（`causes` / `leads_to` / `triggered_by`）建立记忆拓扑连接；
  - 双向图推演与环路熔断：支持向前顺推下游次生引发链与逆向回溯根因诱因链，内置 `visited` 环路截断防护，防止无限递归；
  - 租户行级安全隔离：所有拓扑跳跃严格校验属主权限与 `viewer_of` 租户边界，杜绝跨租户嗅探私有图谱；
  - 交付端到端不 mock 冒烟测试 `tests/test_gewu_causal_topology_smoke.py` 与 `tests/test_gewu_causal_engine_smoke.py`。
- **事件溯源链 API 与查询接口及决策脉络回溯（票 02，详见 `lantai/cognition/causal.py`、`lantai/cognition/context.py` 与 `tests/test_gewu_attribution_api_smoke.py`）**：
  - 高阶决策归因入口：实现 `CausalEngine.attribute_decision`，支持基于显式 `memory_id` 或自然语言 `query` 模糊检索定位决策，生成初始根因、逐级链路与正序时间线叙事；
  - 认知上下文切面打通：在 `CognitiveContext` 扩展 `causal_provenance` 切面属性，并在 `to_prompt()` 中渲染格式化 Markdown 因果决策提示块；
  - API 与 MCP 双形态端点集成：打通 HTTP 路由 `GET /edges/provenance` 与 `GET /edges/timeline`，验证 MCP 标准工具 `causal_trace` 与 `event_timeline` 的 JSON-RPC 2.0 调用及越权隔离；
  - 交付端到端不 mock 冒烟测试 `tests/test_gewu_attribution_api_smoke.py`。

## [0.34.0] - 2026-10-29 - 探颐（Tanyi · 探赜索隐）

> 版本代号「探颐」：《易·系辞上》「探赜索隐，钩深致远」——深入探求隐微之理，对应本版主题「探颐（Tanyi · 记忆主动探针与自然交互消歧机制，ADR-0013 / ADR-0037 / docs/future-roadmap.md 方向一落地）」：改变记忆系统只被动查询的现状，构建认知层主动求证探针引擎 `TanyiProbingEngine` 与上下文插桩模型 `ProbeInsertion`，当检索召回命中未决冲突（`ConflictEvent`）或处于模糊带（`0.3 <= confidence <= 0.6`）的低置信度事实时，自动在 Prompt 中注入轻量求证探针；打通自然交互答复意图分类器 `parse_user_resolution_intent` 与自动闭环消解流水线 `execute_tanyi_resolution`，严密支持肯定（更新正文、版本递增、重置衰减分、落 `MemoryCheckpoint` 快照、同步 FTS/向量索引与解决冲突）、否定（废弃冲突保留原主张）、事实修正（提取自然输入新事实并同步索引）及模糊暧昧（保持 pending 绝不脏写），全流程严守行级 ACL 403 越权防护与多租户隔离。登记见 [ADR-0013](docs/adr/0013-naming-system.md) §7 与 `CONTEXT.md` 词汇表。票据 `.scratch/v0.34-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **探颐认知层主动求证探针引擎与插桩服务（票 01，详见 `lantai/cognition/tanyi.py`、`lantai/cognition/__init__.py` 与 `tests/test_tanyi_probing_smoke.py`）**：
  - 核心引擎设计：落地 `TanyiProbingEngine` 与插桩数据结构 `ProbeInsertion`，支持按 query 语义相关性及召回内容动态扫描未决冲突与模糊带记忆；
  - 上下文插桩格式化：实现 `format_context`，生成温和自然的求证提示块，促使智能体在回复中顺带向用户求证；
  - 租户隔离与属主防御：非管理员严格限制仅能看见同租户且属于自身的记忆与未决冲突探针；
  - 交付端到端不 mock 冒烟测试 `tests/test_tanyi_probing_smoke.py`。
- **自然交互答复解析与自动 conflict_resolve 闭环（票 02，详见 `lantai/cognition/tanyi.py`、`lantai/cognition/__init__.py` 与 `tests/test_tanyi_resolution_smoke.py`）**：
  - 自然语言意图分类器：实现 `parse_user_resolution_intent`，严谨区分肯定（`AFFIRMATIVE`）、否定（`NEGATIVE`）、新事实修正（`CORRECTION`）与模糊暧昧（`AMBIGUOUS`）；
  - 自动消歧闭环流水线：实现 `execute_tanyi_resolution`，肯定意图原子化采纳新事实并落 `MemoryCheckpoint` 快照、递增版本并强一致同步全文/向量索引；否定意图标记 `dismissed` 保留原状；修正意图精准提取用户纠正的新文本写入；模糊意图执行推迟，绝不脏写；
  - 严格权限校验：调用 `ensure_can_delete` 验证行级属主与租户，非属主越权消解严密抛出 403 阻断；
  - 交付端到端不 mock 冒烟测试 `tests/test_tanyi_resolution_smoke.py`。

## [0.33.0] - 2026-10-29 - 悬镜（Xuanjing · 记忆星图观测台）

> 版本代号「悬镜」：《旧唐书·魏徵传》「以铜为镜，可以正衣冠；以古为镜，可以知兴替；以人为镜，可以明得失」——高悬明镜，照见万物脉络与因果溯源，对应本版主题「3D 神经元记忆星图与可视化观测台（ADR-0013 / ADR-0059 / docs/future-roadmap.md 方向四落地）」：构建记忆星图拓扑聚合服务 `xuanjing_service.py`，抽取节点活跃引力质心（`weight`）与艾宾浩斯时效衰减率（`decay_score`），融合格物因果拓扑边（`causes` / `leads_to` / `triggered_by`）与关联边（`supports` / `refines` / `contradicts` / `supersedes`），并按记忆场景星系（`MemoryScene`）与分轨（`lane`）聚合聚簇；坚守多租户行级安全边界，支持同袍联邦公共知识殿堂（`swarm_shared`）记忆的继承透视；在 REST API（`GET /graph/xuanjing`）与 MCP 工具池（`get_xuanjing_graph`，工具池扩充至 64 个）全面暴露；在 Lantai Web Studio 控制台（`lantai/api/ui/`）集成专有「悬镜星图」观测台面板（`#xuanjingView` 与 `xuanjing.js`），支持 D3 力导向仿真天体投射、恒星衰减高斯光晕滤镜、格物因果火橙连线、多维过滤检索与抽屉式节点深度透视，适配吉金与漏窗双主题。登记见 [ADR-0013](docs/adr/0013-naming-system.md) §7、[ADR-0059](docs/adr/0059-xuanjing-memory-topology-service.md) 与 `CONTEXT.md` 词汇表。票据 `.scratch/v0.33-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **悬镜星图聚合服务与 3D/力导向拓扑数据端点（票 01，详见 `lantai/services/xuanjing_service.py`、`lantai/api/routes_graph.py`、`lantai/cli/mcp.py` 与 `docs/adr/0059-xuanjing-memory-topology-service.md`）**：
  - 架构决策：制定并落地 ADR-0059，规范 3D 神经元记忆星图拓扑模型、恒星质心权重公式与时效光晕衰减映射；
  - 聚合服务：实现 `build_xuanjing_topology`，高效计算节点引力权重与时效保留分，提取包含格物因果关系在内的拓扑有向边，支持星系聚簇与质心统计；
  - 租户隔离与同袍放行：严格维护租户防火墙，允许智能体无缝观测同租户公共知识殿堂（`swarm_shared`）恒星；
  - 接口扩展：暴露 HTTP 端点 `GET /graph/xuanjing`，并在 MCP 工具池注册 `get_xuanjing_graph`（扩充至 64 个工具）；
  - 交付端到端不 mock 冒烟测试 `tests/test_xuanjing_topology_smoke.py`。
- **UI 控制台集成「悬镜」记忆星图观测台面板（票 02，详见 `lantai/api/ui/xuanjing.js`、`lantai/api/ui/index.html`、`lantai/api/ui/styles.css` 与 `lantai/api/routes_ui.py`）**：
  - 观测台工作区：在控制台导航栏与工作区主体接入「悬镜星图」视图 `#xuanjingView`，配备指标统计条与分轨/数量多维过滤控制器；
  - D3 拓扑仿真引擎：实现 `xuanjing.js`，借助 D3 Force Simulation 模拟天体引力拓扑动力学，将艾宾浩斯记忆保留度映射为高斯模糊恒星光晕，将因果边高亮为因果火橙色，标识同袍公共殿堂外环；
  - 节点透视抽屉：支持点击任意恒星节点唤起侧边抽屉，实时查阅记忆原旨、所属分轨、引力质心、衰减时效与被引频次；
  - 双主题与静态托管：在 `routes_ui.py` 注册静态托管映射，并在 `styles.css` 完善吉金（暗色深空）与漏窗（明亮星盘）双主题渲染及平滑过渡；
  - 交付端到端不 mock 冒烟测试 `tests/test_xuanjing_ui_smoke.py`。

## [0.32.0] - 2026-10-29 - 同袍（Tongpao · 记忆联邦）

> 版本代号「同袍」：《诗经·秦风·无衣》「岂曰无衣？与子同袍」——多智能体协同、守望相助，对应本版主题「多智能体记忆联邦与公共知识殿堂、两级分级存储与跨智能体技能结晶晋升共享（ADR-0013 / ADR-0058 / docs/future-roadmap.md 方向三落地）」：在 `MemoryItem` 与 `SkillCrystal` 实体引入两级存储范式 `shared_scope`（`private` 私有工作区 / `swarm_shared` 公共知识殿堂）；设计并落地 SQLite 增量迁移 `_migrate_v35`；扩展混合检索（`hybrid_search`、`_keyword_fallback` 与 FTS5 全文检索）在保证租户安全隔离前提下，为指定 Agent 自动放行继承公共殿堂知识；落地 SkillCrystal 晋升评考流水线（`crystal_promoter.py`），遵循「宁 miss 不脏写」原则核验有效规程步骤与成功验证频次，驱动技能晋升为 `MemoryItem(lane='procedural')` 并自动构筑 `MemoryEdge(relation='leads_to')` 因果溯源边；在 REST API（`POST /crystals/{crystal_id}/promote`）与 MCP 工具池（`crystal_promote`，工具池扩充至 63 个）完整暴露。登记见 [ADR-0013](docs/adr/0013-naming-system.md) §7 与 `CONTEXT.md` 词汇表。票据 `.scratch/v0.32-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **多智能体记忆联邦分级存储与公共知识殿堂隔离（票 01，详见 `lantai/models/tables.py`、`lantai/storage/migrations.py`、`lantai/retrieval/hybrid.py` 与 `lantai/storage/fts.py`）**：
  - `MemoryItem` 实体新增 `shared_scope` 索引列（`private` / `swarm_shared`）；
  - `_migrate_v35` 补齐两级分级存储列与索引，平滑兼容旧数据；
  - `hybrid_search`、`_keyword_fallback` 与 `FTS5` 全文索引层重构查询策略，实现“本 Agent 私有记忆 + 全 Swarm 公共记忆”联合召回，同时坚守多租户（Tenant）强隔离防火墙；
  - 交付端到端不 mock 冒烟测试 `tests/test_tongpao_federation_smoke.py`。
- **SkillCrystal 技能结晶跨 Agent 晋升评考与继承（票 02，详见 `lantai/evolution/crystal_promoter.py`、`lantai/api/routes_crystals.py` 与 `lantai/cli/mcp.py`）**：
  - `SkillCrystal` 实体扩展 `success_count`、`source_agent_id` 与 `shared_scope`；
  - 落地 `evaluate_crystal_for_promotion` 评考机制与 `promote_crystal` 晋升流水线，严谨验证规程合法性与命中频次；
  - 晋升时生成 `MemoryEdge(relation='leads_to')` 因果时序拓扑溯源边，记录技能从 Agent 私有结晶晋升为群体智慧的全过程；
  - 暴露 HTTP 路由 `POST /crystals/{crystal_id}/promote`；
  - MCP 工具池新增 `crystal_promote` 工具（工具池规模增至 63 个），全面支持 Agent 间技能晋升与继承；
  - 交付端到端不 mock 冒烟测试 `tests/test_tongpao_crystal_promotion_smoke.py` 并保证 MCP 工具集全绿。

## [0.31.0] - 2026-10-29 - 格物（Gewu · 格物穷理）

> 版本代号「格物」：《礼记·大学》「致知在格物，物格而后知至」——穷究事物原理与因果脉络，对应本版主题「因果时序拓扑与事件溯源链、MemoryEdge 因果关系建模与归因推理闭环（ADR-0013 / docs/future-roadmap.md 方向二落地）」：扩展图谱边关系枚举 `EdgeRelation`（`causes` / `triggered_by` / `leads_to`）与关联常量 `CAUSAL_RELATIONS`，支持因果有向图落盘；落地事件溯源服务 `trace_decision_provenance` 与 `get_event_timeline`，支持面向决策/状态变更反查前置诱因、定位顶层根因并按真实事件时间戳（`event_time` / `created_at`）严格正序组织时间线；内置图谱遍历环路安全阻断与租户行级 ACL 隔离；在 MCP 工具池（`causal_trace`、`event_timeline`）与 HTTP 路由（`GET /edges/provenance`、`GET /edges/timeline`、`GET /edges/{memory_id}/causal-chain`）完整暴露。登记见 [ADR-0013](docs/adr/0013-naming-system.md) §7 与 `CONTEXT.md` 词汇表。票据 `.scratch/v0.31-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **格物因果关系建模与 MemoryEdge 拓扑扩展（票 01，详见 `lantai/models/enums.py`、`lantai/models/tables.py`、`lantai/storage/edges.py` 与 `lantai/services/edge_service.py`）**：
  - `EdgeRelation` 新增 `causes`、`triggered_by`、`leads_to`，并在 `tables.py` 注册 `CAUSAL_RELATIONS` 冻结常量集合；
  - 存储层与服务层实现 `get_causal_chain`，支持顺查下游事态（`forward`）与逆查根因溯源（`backward`），集成访问控制阻断与闭环死循环熔断；
  - 暴露 HTTP 路由 `GET /edges/{memory_id}/causal-chain`；
  - 交付端到端不 mock 冒烟测试 `tests/test_gewu_causal_topology_smoke.py`。
- **事件溯源链查询与决策因果归因闭环（票 02，详见 `lantai/services/provenance_service.py`、`lantai/api/routes_edges.py` 与 `lantai/cli/mcp.py`）**：
  - 落地 `trace_decision_provenance`，支持按 `memory_id` 显式输入或自然语言 `query` 模糊推导定位目标决策记忆，反溯根因节点 `root_cause_id` 并生成时序脉络摘要；
  - 落地 `get_event_timeline`，以节点为中心正反双向扩展因果边，按发生时间严格升序编排事件时间线；
  - 暴露 HTTP 路由 `GET /edges/provenance` 与 `GET /edges/timeline`；
  - 扩充标准 MCP 工具池至 62 个（新增 `causal_trace` 与 `event_timeline`），支持租户隔离；
  - 交付端到端不 mock 冒烟测试 `tests/test_gewu_event_provenance_smoke.py` 并通过全量 MCP 门禁。

## [0.30.0] - 2026-10-29 - 探颐（Tanyi · 探赜索隐）

> 版本代号「探颐」：《易·系辞上》「探赜索隐，钩深致远」——深入探求隐微之理，对应本版主题「记忆主动探针与自然交互消歧机制（ADR-0037落地）、模糊带事实与未决冲突的主动澄清求证与闭环裁决」：落地核心服务 `detect_memory_probes` 与 `format_probing_context`，检索时扫描悬决冲突与临界模糊带（0.3 <= confidence <= 0.6）事实并生成温和澄清探针；打通次轮自然答复闭环 `resolve_probe_response`，支持肯定意图（应用新事实、版本更替、落 Checkpoint 快照与同事务双索引强一致）、否定意图（废弃冲突保留原状）与中立/转移话题（严格遵循宁 miss 不脏写）；在 MCP（`probe_detect`、`probe_resolve`）与 REST 路由完整暴露。登记见 [ADR-0013](docs/adr/0013-naming-system.md) §7 与 `CONTEXT.md` 词汇表。票据 `.scratch/v0.30-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **探颐核心服务与主动消歧探针生成（票 01，详见 `lantai/services/probing_service.py` 与 `lantai/core/settings.py`）**：
  - 新增全局开关 `ENABLE_PROACTIVE_PROBING: bool = True`，并在 `tests/test_settings_defaults_frozen.py` 冻结出厂默认值；
  - 增强 `detect_memory_probes`，支持悬决冲突与临界模糊带（0.3~0.6）事实/记忆双维度扫描，防重复压制，严格租户隔离；
  - 增强 `format_probing_context` 格式化规范，渲染 `【探颐·待求证事项】` 提示块供回复决策参考；
  - 交付不 mock 核心逻辑的冒烟测试 `tests/test_tanyi_probing_smoke.py`。
- **探颐自然交互闭环与冲突自动裁决（票 02，详见 `lantai/services/probing_service.py`、`lantai/cli/mcp.py` 与 `lantai/api/routes_probing.py`）**：
  - 升级 `resolve_probe_response`，双向兼容 `conflict_id` 与 `probe_id`，覆盖肯定、否定与中立意图；
  - 肯定意图驱动正文替换、版本递增、Checkpoint 审计留痕与 FTS/Chroma 强一致同步；
  - 否定意图软废弃冲突，中立意图安全推迟（宁 miss 不脏写）；
  - 暴露 MCP 工具与 API POST 路由；
  - 交付端到端不 mock 冒烟测试 `tests/test_tanyi_resolve_smoke.py`。

## [0.29.0] - 2026-10-29 - 磨勘（Mokan · 磨勘功过）

> 版本代号「磨勘」：《宋史·职官志》审官院核验官员功绩以定升降，磨勘功过、自愈长青。贴合本版主题「MCMA 建议项落地：认知抽象三层架构选层检索、4~5 条知识注入硬顶与信息密度优先重排」：落地 ADR-0058 记忆认知分层与意图映射机制（H0 对话观察 / H1 情境事实 / H2 核心准则与反思总结），打通意图识别驱动的动态选层过滤与分层得分加权；依据 MCMA 论文实证落地知识注入硬顶控制（`SHELL_HOOK_MAX_INJECT_ITEMS: 5`），严格限制注入条目防注意力分散，完璧原子事实多实体整块保整计 1 项，并在候选溢出时结合认知层级开展信息密度重排。登记见 [ADR-0013](docs/adr/0013-naming-system.md) §7 与 `CONTEXT.md` 词汇表。票据 `.scratch/v0.29-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **抽象层级化与意图选层检索闭环（票 01，详见 `lantai/retrieval/hierarchical.py`、`lantai/retrieval/hybrid.py` 与 `scripts/shell_hook.py`）**：
  - 新增 `HierarchicalLayer`（H0/H1/H2）与认知角色映射；
  - 扩展意图识别结果，支持按意图（如 rule_lookup -> H2, fact_lookup -> H1, general_chat -> H0+H1+H2）精确选层与加权过滤；
  - 在 `hybrid_search` 中引入层级自适应权重提升与过滤闭环；
  - 交付不 mock 核心逻辑的冒烟测试 `tests/test_mokan_hierarchical_layer_smoke.py`。
- **4~5 条知识注入硬顶与信息密度重排（票 02，详见 `lantai/retrieval/hierarchical.py`、`scripts/shell_hook.py` 与 `lantai/core/settings.py`）**：
  - 新增配置 `SHELL_HOOK_MAX_INJECT_ITEMS: int = 5`，强制注入端条目数恒 $\le 5$ 铁律；
  - 多实体完璧原子事实整块保整，计数严格为 1 个知识单元；
  - 新增 `rerank_by_information_density` 机制，按 H2(1.25x) > H1(1.00x) > H0(0.80x) 与原子事实增益优先截断保留高价值知识；
  - 交付不 mock 内部计算逻辑的冒烟测试 `tests/test_mokan_items_budget_smoke.py`。

## [0.28.0] - 2026-10-29 - 稽古（Jigu · 曰若稽古）

> 版本代号「稽古」：《尚书·尧典》「曰若稽古」——考订古今、时态立心。贴合本版主题「as-of 双时间轴时态查询引擎全链路打通、历史切片精确回放、迟到更正时态消解（ADR-0048）与 Hermes 插件生产级注入改造（Ticket 06）」：打通 `wanbi_retriever` 与 `hybrid_search` 在任意历史时点（`as_of`）的认知状态切片，严禁未来事实污染历史回放；实现迟到更正（supersede）的时态消解，旧事实自动以更正时间点（anchor）对齐失效，并遵循 I3 时态防倒挂钳制与审计留痕；彻底改造 Hermes 插件接入混合检索、启发式闸门与常驻个人画像，消除写死路径与 MCP 冗余。登记见 [ADR-0013](docs/adr/0013-naming-system.md) §7 与 `CONTEXT.md` 词汇表。票据 `.scratch/v0.28-next-window-2026-11/`（票 01/02 全部收口，03 发布门禁）。

### Added

- **as-of 时态查询引擎与完璧原子事实历史快照（票 01，详见 `lantai/retrieval/wanbi_retriever.py` 与 `lantai/retrieval/hybrid.py`）**：
  - `search_atomic_facts_by_query` 与 `hybrid_search` 全链路接入 `as_of` 时态切片参数，支持 `(valid_from IS NULL OR valid_from <= as_of) AND (valid_to IS NULL OR valid_to > as_of)` 严格过滤；
  - 杜绝未来新事实或后续更正事实污染历史切片回放；
  - 交付不 mock 核心计算逻辑的冒烟测试 `tests/test_jigu_as_of_smoke.py`。
- **迟到更正时态消解与双时间轴历史无污染切片闭环（票 02，详见 `lantai/services/record_ops_service.py` 与 `lantai/cognition/late_correction.py`）**：
  - 新增 `supersede_fact_atomic` 核心原语，旧事实 `valid_to` 精准锚定更正时刻，新事实 `valid_from` 起始于该时刻，遵循 I3 时态钳制（防 `valid_to < valid_from` 倒挂脏写）；
  - 在 `apply_supersede_correction` 中引入谓词感知的原子事实级联时态对齐，同事务原子化提交；
  - 注册 `AUDIT_ACTIONS` 的 `"supersede"` 动作并以 `genglou:supersede_fact_atomic` 留痕；
  - 交付不 mock 核心逻辑的冒烟测试 `tests/test_jigu_late_correction_smoke.py`，验证事务时间（当前写入时刻）与事件时间（历史生效区间）解耦。
- **Hermes 插件生产级注入与常驻画像改造（Ticket 06，详见 `scripts/shell_hook.py` 与 `hermes-plugin/lantai-hook/__init__.py`）**：
  - 将粗召回切换至 `hybrid_search` 混合检索与 `relevance_check` 启发式闸门，设置 `SHELL_HOOK_SCORE_THRESHOLD = 0.5` 置信度下限过滤；
  - 落地 `_build_resident_persona`，置顶常驻注入 `user_profile.md`、器识 PersonaProfile 与 CoreMemoryBlock，解决隐式偏好短句（“老样子/照旧”）跳过检索问题；
  - 移除硬编码路径，实现 Python 虚拟环境与插件目录的自适应动态探测；
  - 瘦身 MCP 工具集（`LANTAI_MCP_PROFILE=hermes` 仅暴露 5 个核心工具）；
  - 交付增量 SQLite 迁移 `_migrate_v33`（role 列）与 `_migrate_v34`（session_id 列）。

## [0.27.0] - 2026-10-28 - 贯珠（Guanzhu · 如贯珠焉）

> 版本代号「贯珠」：《汉书·景十三王传》「如贯珠焉」——联络贯穿、珠玉成串。贴合本版主题「完璧多实体原子事实接入 Hybrid 混合检索主流水线、贯珠图谱二度扩散升级与笔削生命周期闭环」：打通事实反查与投影挂载，消除全局盲目广播；升级贯珠图谱关联二度拓扑扩散，消除循环 N+1 查询；落实笔削（ADR-0047）在完璧原子事实上的四分法（就地改文、撤回止召回、级联物理删除）；构建复杂多元实体关系专项评测套件，达成多实体事实语义保真率 100% 与撤回 0 召回。登记见 [ADR-0013](docs/adr/0013-naming-system.md) §7 与 `CONTEXT.md` 词汇表。票据 `.scratch/v0.27-next-window-2026-11/`（票 01/02/03/04 全部收口，05 发布门禁）。

### Added

- **完璧多实体原子事实接入 Hybrid 混合检索（票 01，详见 `lantai/retrieval/wanbi_retriever.py` 与 `lantai/retrieval/hybrid.py`）**：
  - 新增 `search_atomic_facts_by_query`，支持实体分词提取精准反查 `FactEntity` 与 `statement` 模糊包含匹配，严格保障 `confidence > 0` 及有效期，消除内存全表扫描；
  - 在常规向量+FTS检索、Rerank分支以及FTS降级分支接入 `_attach_wanbi_facts`，按来源记忆血统（`source_memory_id`）精准挂载完整原子事实及关联实体列表，杜绝盲目全局广播；
  - 交付不 mock 核心计算逻辑的冒烟测试 `tests/test_wanbi_hybrid_smoke.py`。
- **贯珠图谱二度拓扑扩散升级（票 02，详见 `lantai/retrieval/graph_retriever.py`）**：
  - 改造图谱联想机制，从种子记忆扩展时沿 `FactEntity` 共同包含的原子事实实现二度拓扑扩散；
  - 批量预查投影消除循环 N+1 查询，严格遵循 ACL 租户隔离纪律（`_fact_scope`）；
  - 交付不 mock 核心计算逻辑的图谱扩散冒烟测试 `tests/test_wanbi_guanzhu_smoke.py`。
- **完璧原子事实的笔削生命周期联动（票 03，详见 `lantai/services/record_ops_service.py`）**：
  - 新增 `correct_fact_atomic`（就地纠错改写 statement、重建投影并记录审计留痕）、`retract_fact_atomic`（软撤回：清空投影索引阻断检索扩散、置信度归零、记录时间戳与审计事件）与 `delete_fact_atomic`（物理删除事实并级联清除实体投影）；
  - 在 `retract_memory` 中联动级联软下线关联的原子事实，确保检索端命中为 0；
  - 交付不 mock 内部逻辑的生命周期联动冒烟测试 `tests/test_wanbi_bixiao_smoke.py`。
- **完璧长程多实体关系评测集构建与回归（票 04，详见 `tests/test_wanbi_benchmark_suite.py`）**：
  - 覆盖联合创办、多方签约、代码移交、战略备忘录等复杂多元实体关系用例；
  - 验证多元复杂关系事实语义保真率达 100%，撤回后在检索面实现确定性 0 命中。

## [0.26.0] - 2026-10-28 - 完璧（Wanbi · 完璧归赵）

> 版本代号「完璧」：《史记·廉颇蔺相如列传》「完璧归赵」——璧不受损、完整交付。贴合本版主题「多实体事实原子性保整（ADR-0057落地）与方案B反思监控闭环」：彻底根治多实体复合事实被拆解为二元短语导致语义结构散落的架构缺陷，实现权威事实原子表 `fact_atomic` 与级联实体投影表 `fact_entity`（user_version 31）；抽取约束落地与解析结构化防御；打通保整事实投影反查；完成方案 B 反思 worker 端到端执行与 Daily Digest / `/stats` 健康透出。登记见 [ADR-0013](docs/adr/0013-naming-system.md) §7 与 `CONTEXT.md` 词汇表。票据 `.scratch/v0.26-next-window-2026-11/`（票 01/02/03/04 全部收口，05 发布门禁）。

### Added

- **完璧（ADR-0057）多实体事实原子性存储与迁移（票 01，详见 `lantai/models/tables.py` 与 `lantai/storage/migrations.py`）**：
  - 定义 `FactAtomic`（权威事实主表：含 statement 完整事实句、predicate 谓词、object 中心宾语等）与 `FactEntity`（实体索引投影表：带 `fact_id` 级联删除与 `role` 角色）；
  - 增补 SQLite `_migrate_v31` 增量迁移函数，平滑自建主表、投影表与四大索引，目标 Schema 提升至 v31；
  - 交付不 mock 内部逻辑的存储层冒烟测试 `tests/test_wanbi_schema.py`（覆写级联外键约束、CRUD 与迁移幂等）。
- **完璧提取约束与写入纪律（票 02，详见 `lantai/llm/prompts.py` 与 `lantai/parsing/extractor.py`）**：
  - 更新 `EXTRACT_SYS` 提示词，严禁将涉及 ≥3 实体的事实拆碎为二元短语，规范输出 `atomic_facts` 结构；
  - 在 `extract_candidate` 正常解析与异常降级链路中均增加 `atomic_facts` 兜底键防御（遵循「宁 miss 不脏写」）；
  - 编写不 mock 抽取冒烟测试 `tests/test_wanbi_extract.py`。
- **完璧实体检索与投影查询（票 03，详见 `lantai/retrieval/wanbi_retriever.py`）**：
  - 实现 `get_facts_by_entity` 与 `get_facts_by_predicate`，支持带归属权限过滤的反查与全量实体投影聚合；
  - 编写不 mock 检索冒烟测试 `tests/test_wanbi_retriever.py`。
- **方案 B 反思调度自愈与健康透出（票 04，详见 `lantai/workers/digest_worker.py` 与 `lantai/api/routes_health.py`）**：
  - Daily Digest 每日盘点报告中新增「记忆健康透出」专栏，集中呈现 `superseded_active` / `expired_active` / `open_conflicts` / `low_helpful` / `stale_low_value` 指标；
  - `/stats` 端点透出 `health_snapshot`，实现司天大盘实时健康感知；
  - 编写反思 worker 端到端不 mock 冒烟测试 `tests/test_reflect_worker_smoke.py`。

## [0.25.0] - 2026-10-28 - 嘉量（Jialiang · 嘉量既成）

> 版本代号「嘉量」：《周礼·考工记》「嘉量既成，以观四方」——标准量器、定权度量。贴合本版主题「把评测仪器磨准」：LongMemEval 覆盖率 0.80 仪器量测失真根因审计与修复（rev1 三配置 coverage 1.00，recall 逐位不变）、E1 提取语言漂移提示词与任务框架双语锁定（retrieve 段 0.25→0.75）、MemProbe suite56 认知稳定性-可塑性（SP-Balance）新基准纳入与分层报告交付、对标基线换血为 aiduMEI f0.6 差距分析。登记见 [ADR-0013](docs/adr/0013-naming-system.md) §7 与 `CONTEXT.md` 词汇表。票据 `.scratch/v0.25-next-window-2026-10/`（票 01/02/03/04 入版，05 条件未触发转 resolved，06 发布门禁）。

### Added

- **MemProbe suite56 认知基准纳入（票 03，详见 `docs/benchmarks/memprobe-report-2026-10.md`）**：
  - 黑盒 adapter（`scripts/memprobe_lantai_adapter.py`）接入 MemProbe 官方评测套件，通过 `/import/jsonl` 逐字通道与 `/search` 混合检索（长程时序与角色前缀保真）；
  - 交付全量 56 题（224 探针）三键对照消融（`lantai_verbatim_full`、`full_context`、`naive_rag`）；
  - 在同模型 Qwen3-8B 下，兰台整体准确率达 **62.95%**（逼近完整上下文理论上限 65.18% 的 96.6%）；在再巩固范式上超越完整上下文 **3.58 个百分点**（64.29% vs 60.71%），历史追溯保真度达 **96.43%**；来源识别（23.53% vs 32.35%）如实披露逐字直存结构性边界；
  - 留证入仓：`docs/benchmarks/memprobe-2026-10/` 含预注册、模型身份、数据集校验、逐题回执与终验对账（`verify.json` verdict: OK）。
- **aiduMEI f0.6 新基线差距分析（票 04，详见 `docs/research/aidumei-f06-gap-analysis-2026-10.md`）**：替换陈旧的 v21.2 调研，从留证纪律、失败分级、预算参数、Windows 边界、双轨发布五个维度全面重构上游差距评估与技术演进映射。

### Fixed

- **LongMemEval 映射保真度覆盖率修复（票 01，详见 `docs/benchmarks/longmemeval-2026-10/REVISION-2026-10-27.md`）**：
  - 逐题审计确认 0.80 覆盖率为评测 harness 将 top-5 误当已灌清单之仪器缺陷，金标在库实为 0 缺失；
  - 修复灌库序列化与覆盖率在场性探针判据，三配置覆盖率全部校准至 **1.00**，recall@2（0.84）与 recall@5（0.88）逐位不变，产出 rev1 修订留证。
- **E1 提取语言漂移提示词双语锁定（票 02，详见 `docs/benchmarks/e1e2-lang-drift-2026-10/REVISION-E1-LANGUAGE-LOCK.md`）**：
  - 根因分析揭示小模型易将中文对话摘要为英文笔记导致跨语言检索断流；
  - 提取提示词 `EXTRACT_SYS` 与用户框架模板强化中文输出锁定（`lantai/llm/prompts.py`、`lantai/parsing/extractor.py`）；
  - E1 复跑真 LLM 轮 retrieve 段由 **0.25 恢复至 0.75**（达到替身轮理论上界），单元测试 `tests/test_extract_language_lock.py` 锁定防护。

## [0.24.1] - 2026-10-27

> 补丁版（免代号，沿 v0.23.1–v0.23.3 先例）：发布节奏新规下首个「留证钉板」版——把评测自证线的公开报告锚定到不可变 tag。生产路径行为零变更（改动面为评测 harness、脚本、文档与基准留证）。

### Added

- **评测自证线收口（票 02 步骤②-⑤，详见 `docs/benchmarks/selfproof-report-2026-10.md`）**：对表预期标签外置（`staged-labels-2026-10.json`）+ 带证据字段的对表脚本；E1/E2 正式跑测（Qwen3-8B 钉板 + 确定性替身对照；提取段 3 报一致率 0/10 如实披露；语言漂移第一发现）；LongMemEval-S cleaned 25 题预注册 + 双闸灌库 + 三配置消融（**hybrid recall@5 0.88（条件 1.00）> bm25 = vector 0.84**，覆盖率 0.80 同报），全留证入仓。
- **前置修复**：`scripts/run_staged_eval.py` 混维断言；确定性 embed 单一真源（`lantai/eval/deterministic_embed.py`）。
- **ADR-0057「完璧」（Proposed 草案）**：多实体事实抽取粒度原子性，SQLite 表达方案，命名已登记 `CONTEXT.md`。
- **发布节奏成文**：`docs/release-process.md` 新增「发布节奏」节（窗口版 minor + 两类免代号补丁触发 + 3 周软上限）。

## [0.24.0] - 2026-10-25 - 悬衡（Xuanheng · 悬衡而知平）

> 版本代号「悬衡」：《韩非子·饰邪》「悬衡而知平，设规而知圆」——悬起衡器方能校准曲直。贴合本版主题：为下一程评测自证校准仪器（评测替考后门测试锁定、司天 LLM 降级率与 git_sha 观测补全、episode 影子接线默认关、上游同构雷四路自查无雷）。登记见 [ADR-0013](docs/adr/0013-naming-system.md) §7。票据 `.scratch/v0.24-next-window-2026-10/`（票 01/02 第一步/03/07 入版，02 第二至五步与 04-06/08-09 按「评测线整块推进不半截」判据顺延下一窗口，裁决记录见 `map.md`）。

### Added

- **episode credit 影子接线（默认关，ADR-0056）**：检索命中记忆的累计轨迹信用作为乘性加权信号 `score × (1 + 0.1·tanh(credit))`；`EPISODE_CREDIT_ENABLED` 默认 `False`——水源（dogfooding 回执）未解、样本不足不调权（2026-09 报告 episode 影子纪律）。关闭时取数前短路，episode 表一次都不碰；默认关逐位一致回归在冻结时钟下验证，三变异门禁（翻默认/改系数/关闭态仍读表）全红。explain 新增 `episode_credit` 字段。测试 `tests/test_episode_credit_shadow.py` 5 例（真实 SQLite 直调主路径）。
- **司天 24h LLM 降级率一级指标（票 J-a）**：新增 `llm_call_event` 表（迁移 v30，只记 channel/outcome/duration 标量、零正文零提示词），`lantai/llm/client.py` 三外呼通道（chat/vision/embed）结局记录（tenacity 重试耗尽记 error，尽力而为不反噬外呼）；`collect_llm_degrade` 三态判定（no_evidence/ok/degraded，零调用不装绿），快照一级键 `llm_degrade`，高降级告警 `llm_degrade_high`（≥10 次且失败率 ≥50% 判 high）。测试 `tests/test_llm_degrade_and_git_sha.py` 8 例。
- **/health 返回 git_sha（票 J-b）**：PATH→常见安装路径回退定位 git、60s 短缓存、取不到返回 null 绝不抛错（Docker 镜像内可能无 git）；不影响 ok 判定。
- **评测替考后门测试锁定（票 02 第一步收口）**：`tests/test_staged_anchor_miss.py` 3 例直调 `run_staged_eval` 全六段（真 SQLite+FTS5+内嵌 ChromaDB，替身仅外部 embedding 面），锁定「锚点缺席绝不替考补位、必报 achieved=False + redirected 指向实际拦截段」；三变异门禁（恢复 `or indexed[:1]` 兜底/缺席误报 achieved/去掉 redirected）全红。替考后门本体经复核确认已于 2026-10-10 修复（`staged.py:498` 无兜底），本版补上测试锁。

### Documentation

- **ADR-0055（票 I）**：API key 哈希维持 SHA-256 的条件升级备忘——key 为 `token_urlsafe(32)` 256 bit 熵，加盐/多轮不改变可计算攻击成本；三条触发即重开的升级条款（自设弱 key/熵源变更/哈希介质扩散）。现状零代码变更。
- **ADR-0056**：episode 影子接线决策（默认 false 理由、加权公式、开启条件、回滚方式）。
- **版本代号「悬衡」**：登记 `CONTEXT.md` 词汇表与 ADR-0013 §7。

### Verified

- **上游同构雷四路自查（票 01，目检无雷）**：对照上游 aiduMEI f0.6 四真雷逐项目检兰台代码——遗忘时间基准（更漏 ADR-0048 各落点有实现）、向量参数同源（embed 单一真源+collection 单例）、笔削/起复删除-索引三处一致（读侧 status 过滤兜底）、写入活性探针已在位（`collect_ingest_liveness` 三态+告警+回环自查脚本）。证据全文见票据 01 Comments；「无雷」为兰台侧目检结论，不构成对上游自述的采信。

## [0.23.5] - 2026-10-25 - 鱼贯（Yuguan · 衔枚鱼贯）

> 版本代号「鱼贯」：《三国志·邓艾传》「衔枚鱼贯」——依次而进、不壅不塞。贴合本版主题：遗忘周期改 keyset 分页逐批推进，读路径纯化、单簇容错与全库统计口径收口。登记见 [ADR-0013](docs/adr/0013-naming-system.md) §7。

### Changed

- **遗忘周期 keyset 分页（性能，行为零变更）**：`apply_forgetting` 弃 `s.exec(q).all()` 全表一次性加载，改按 `MemoryItem.id`（前缀 ULID，字典序即稳定全序）keyset 分页，逐批推进游标直至取空；`page_size` keyword-only 可注入（默认 500，小于 1 显式 raise——SQLite 负 LIMIT 视为无界、0 视为空集，静默错参比报错危险），页尾补 commit + `expunge_all()` 使内存随批大小而非库容增长。判定逻辑、衰减公式、归档阈值、`|Δdecay|<0.001` 跳写、每 100 条变更一 commit（DD-06）语义逐字未动。一致性锚：同一份 28 条种子以 `page_size=10`（3 批）与 `page_size=1000`（单页≈旧全量加载口径）逐条 status+decay_score 一致（覆盖衰减更新、到期归档、不动三类）；SQL 形态经 `before_cursor_execute` 只读观测断言无任何无界 memoryitem 查询。新增 `tests/test_forgetting_keyset.py` 3 例（真 SQLite 不 mock）。票据 `.scratch/review-fixes-2026-10/issues/09-forgetting-pagination.md`（0.23.4 票 07 遗留第 4 项，本批补齐）。

### Fixed

- **读路径 ORM 原地突变 + 冲突裁决混时区崩溃**：`hybrid._chronos_filter` 与 `temporal.temporal_view_filter` 的 decay 降权改 `model_copy` 本地副本——真实栈实证副本不进 identity map、不脏 session、commit 后库值不变、`model_dump` 逐字段一致，带 session 的检索调用一旦 commit 不再把降权衰减分永久写回，打分/输出语义逐字不变；`policies.resolve_conflict` 的 created_at 比较前过 `lantai/core/time.py` 的 `ensure_aware`（票面 ensure_utc 在该模块不存在，此为等价既有入口），混 naive/aware 不再 TypeError 被吞，且只取本地值不回写 ORM。读路径 ORM 突变面复查清单留痕票据。新增 `tests/test_readpath_orm_mutation.py` 13 例（直调 detach/session 两态 + 带 session 完整 hybrid_search 真实 FTS/BM25/Chroma 降级路径 + 混时区三态）。票据 `.scratch/review-fixes-2026-10/issues/10-readpath-orm-mutation.md`。
- **沉潜周期单簇 embedding 失败中断整周期**：`run_consolidation_cycle` 簇循环体 per-cluster try/except（失败簇先取碎片 id→回滚半成品事务防搭下一簇 commit 顺车落库孤儿态→异常本体与碎片 id 进 `warnings` 不静默→继续下一簇），`prune_decayed_synapses` 包 try/except，整段 try/finally 保证报告与 `_LAST_CONSOLIDATION_REPORT` 落盘必达；report 增量键 `warnings`（初值/`_refused_report`/运行后三处键集对齐，REST docstring 的 count-only 定级口径如实补注），聚合口径对照 revive 碎片路径「宁缺不糊」先例。新增 `tests/test_consolidation.py` 单簇 embed 故障不 mock 冒烟（第 1/3 簇照常巩固、失败簇碎片保持 active 无半成品残留、prune 照常执行）。票据 `.scratch/review-fixes-2026-10/issues/11-consolidation-per-cluster-guard.md`。
- **digest `other` 计数跨账户串数**：`_aggregate_reflection` 的 `other` 查询补与 created/applied/pending/rejected 四兄弟逐字一致的 `prop_scope` 归属过滤，恢复 `applied+pending+rejected+other == created` 自证不变量，多用户库下 A 视角的 other 不再混入 B/越册行；无 principal 全库口径对在册行计数不变。新增 `tests/test_digest_ownership.py` 归属排除冒烟（真 SQLite 夹具）。票据 `.scratch/review-fixes-2026-10/issues/12-digest-other-ownership.md`。
- **迟到更正 created_at 兜底 naive/aware TypeError 静默失败 + checkpoint 空快照**：`correction_anchor` 的 created_at 兜底分支过 `_ensure_utc`（与 event_time/valid_from 两支及 old_vf 口径对齐），两个 except 增 `logger.warning` 留痕不再无迹可查；supersede/expire 的 `MemoryCheckpoint.before` 改为突变前 `model_dump(mode="json")` 真实快照（对照 promoter 既有口径），回滚该版本有 before 可依。实测勘误：sqlmodel 0.0.47 `UTCDateTime` 使 DB 读回恒为 aware，票据「SQLite 读回 naive」为陈旧 premise，真红形态是内存直传 naive created_at——TypeError 被吞成静默失败，修复于 anchor 源头一并消灭直传 naive 与 naive 落库拒写两种形态。新增 `tests/test_genglou_correction.py` 4 例。票据 `.scratch/review-fixes-2026-10/issues/13-late-correction-timezone-snapshot.md`。
- **checkpoint 全短内容重写清空旧快照（删旧写零）**：`write_session_checkpoint` 校验前置——新增纯函数 `_classify_blocks`（合法组+逐键拒绝明细单一来源，`validate_blocks` 改薄封装对外契约不变），新块全部被拒（全短/全非法/空 blocks）时不碰库、旧快照原样保留，返回 `status="rejected"` + 拒绝明细（新内容不合格 = 本次不写，而非清空历史，合「宁 miss 不脏写」）；有合法新块才走原样同事务 delete+insert。三个消费方（REST/MCP/hook）对返回 dict 原样透传，additive 无破坏。新增 `tests/test_checkpoint_service.py` 2 例；一处依赖旧清空行为的断言按新语义改写（测试原意图保留）。票据 `.scratch/review-fixes-2026-10/issues/14-checkpoint-validate-before-clear.md`。
- **rerank 响应缺失/越界 index 默认 0 错配候选**：`_parse_response` 对缺失/非整型/越界（含负数）/bool（int 子类）index 的返回项跳过并按跳过计数单条 warning 留痕，不再默认 0 脏配污染排序；全部缺失返回 `[]` 走 `hybrid.py` 既有降级链回退混合检索分（拾遗 ADR-0028「宁降级不脏写」口径），合法项行为逐字不变。新增 `tests/test_reranker.py` 5 例 + `tests/test_rerank_index_and_schema.py` hybrid 层不 mock 冒烟 1 例（仅 mock `requests.post` 外部网络）。票据 `.scratch/review-fixes-2026-10/issues/15-reranker-missing-index.md`。
- **低危清扫五项（L 项）**：L1 `hybrid._hybrid_search_impl`/`_keyword_fallback` 四处 `params.` 直接解引用改兜底局部 `p`（票面只点名 strict 两处，同调用点 `fuzzy_penalty` 同根因连坐同修；直调 impl 传 None 不再 AttributeError）；L2 `prefilter._USER_GATE_DECISIONS` 加 TTL(3600s)+容量上限(1024) 清扫（写法对照 coalesce `_seen` 既有写法，实现两轮被测试钉出修正后收敛）；L3 evolve 自动应用阈值 0.7 迁 `settings.EVOLVE_AUTO_APPLY_CONF`（命名对照 `REFLECT_AUTO_APPLY_CONF`，默认不变，冻结默认门禁登记）；L5 `scheduler.record_run` 落库失败 `except: pass` 改 `logger.exception`（内存态兜底与不阻断语义保留）；L6 `ingest_worker` done/failed 两条收尾路径补 job None 防护（换独立变量名防 `job.id` 连坐不可达，按「任务已不存在」warning 留痕跳过，异常不再中断整个 source 循环）。按低危清扫替代口径验收：L1/L3/L6 先红后绿、L2 红→红→绿、L5 caplog 行为测试，定向 155 passed + 静态复验留痕票据。票据 `.scratch/review-fixes-2026-10/issues/16-ltier-hygiene.md`。

## [0.23.4] - 2026-10-24 - 勾检（Goujian · 勾检稽失）

> 版本代号「勾检」：《唐六典》勾官之职「勾检稽失」——稽查文书账目之失误而纠正之。贴合本版主题：全面审查勾检全库，修复冲突账本静默丢失、索引与正文错位、反思重复落库等八项稽失。登记见 [ADR-0013](docs/adr/0013-naming-system.md) §7。

### Changed

- **检索/演化链路去 N+1 与全表加载（行为零变更）**：proposer/reflector 的相关记忆查询 `limit(20)` 下推 SQL（无 ORDER BY 自然序与旧 `[:20]` 切片一致，prompt 逐字不变）；`_importance_waterline` 由全表物化改 SQL `coalesce(sum)` 聚合（不筛 status、NULL 行不计等口径逐字保留）；scene `assign_unassigned` 逐条 embed 改整批一次（50 次 API → 1 次，归属判定只依赖质心故与逐条口径一致，批前失败零副作用更合「宁 miss 不脏写」）。基准（2000 条、7 次取中位）：proposer 73.9→5.3ms、水位 58.3→0.8ms（返回值前后一致）、reflect 空闲 259.7→66.1ms、scene 690.7→122.3ms 且 embed 50 次→1 次。新增行为 pin 测试 4 例（修复前后均绿）。票据 `.scratch/review-fixes-2026-10/issues/07-retrieval-full-table-loads.md`（第 4 项 forgetting 分页游标另行排期，本批未动）。

### Removed

- **弃用从未生效的 COEXIST 冲突共存裁决**：全仓实证不存在 `scope.task_types` 写入方，该机制自诞生从未生效——拆除 `gate/decision.py` 的 ConflictEngine 死仲裁块（硬冲突直断 `s.commit()` + ARCHIVE_CONFLICT，行为零变更，基线测试零改动通过佐证）、`cognition/conflicts.py` 的 `ConflictResolution.COEXIST` 枚举成员、`resolve()` 预检分支与文案分支（六维「直断」主体不动），并删除只测死机制的 `test_conflict_engine_coexist`。决策、证据链与重启条件见 [ADR-0054](docs/adr/0054-deprecate-coexist.md)。票据 `.scratch/review-fixes-2026-10/issues/08-coexist-dead-branch.md`。

### Fixed

- **探颐消解后双索引永不更新**：删除死 import（`lantai.retrieval.embed`）与吞异常 try/except，消解后 FTS 走 commit 前同事务 `sync_fts`（ADR-0008 强一致，失败随事务回滚宁 miss 不脏写），向量重建复用 `record_ops_service.sync_vector_upsert`（替换后新正文），同步结果以 `vector_synced`/`warnings` 如实进回执。新增真栈冒烟 `tests/test_probing_resolution_smoke.py`。票据 `.scratch/review-fixes-2026-10/issues/01-probing-index-resync.md`。
- **每日反思 detect_patterns 无限重复写库**：pattern 侧以聚类核心实词签名 sha256 前 16 位为新列 `cognitivepattern.dedup_key`（迁移 v29，存量行 NULL 宁 miss），同键只 touch `updated_at` 不重复插行、不向下游重发；候选侧以 (role, content) 自然键去重（已否决候选不复活）；修正与行为相反的错误注释。新增 `tests/cognitive/test_pattern_dedup.py` 与 `tests/test_migration_v29_pattern_dedup_key.py`。票据 `.scratch/review-fixes-2026-10/issues/02-reflect-pattern-dedup.md`。
- **闸门冲突账本两条路径静默丢失**：`check_unavailable` 早退前补条件 `s.commit()`（顺带救回同场被回滚的降权 Checkpoint/resolved 账本/importance），COEXIST 放行分支补同粒度 commit；用例 B 经维护者裁决放弃构造（死分支不可达，另立票 08 留痕）。`tests/test_conflict_rules.py` 新增 2 例。票据 `.scratch/review-fixes-2026-10/issues/03-gate-ledger-commit.md`。
- **promoter 回滚不重建向量索引**：rollback 复用 `record_ops_service.sync_vector_upsert` 重建向量（失败 `logger.exception` 留痕 + `vector_synced=False` 如实回报，不阻断回滚主语义）；附带修复快照 datetime 还原（`parse_iso_utc`，非法时间戳拒绝回滚不写半笔）。新增 `tests/test_promoter_rollback_vector.py`。票据 `.scratch/review-fixes-2026-10/issues/04-promoter-rollback-vector.md`。
- **卫生清扫**：删除死代码 `verify_api_key`、`_param_override` 与 `fts_hits` 重复赋值（连同只测死机制的 `TestParamOverrideContext`），`.gitignore` 补 5 目录、`.env.example` 补 3 变量（无真实密钥）；纯删除零行为变更。票据 `.scratch/review-fixes-2026-10/issues/05-hygiene-sweep.md`。
- **认知上下文注入已撤回/归档记忆**：`CognitiveContextBuilder._owned_memory_stmt` 基座统一补 `status == "active"`（admin 分支不豁免，口径同 `hybrid.py` 读侧三处），retracted/archived/consolidated/superseded 记忆不再进入 `/cognitive/context` 与 `/context/prompt` 注入 Agent 提示词；top_k 下推 SQL 经评估不做（Jaccard 为 Python 分词后计算且按 role 切面截断，SQL 无法表达）。新增 `tests/test_cognitive_context_active_filter.py` 3 例（撤回走真实 `retract_memory` 全链路不 mock，断言行仍在库证明是过滤而非删除）。票据 `.scratch/review-fixes-2026-10/issues/06-cognitive-context-active-filter.md`。

## [0.23.3] - 2026-10-14

### Fixed

- wheel 补打 Web UI 静态文件(`lantai/api/ui/*`)。此前 `pyproject.toml` 未声明 `package-data`,setuptools 只打 `.py`,`/ui/` 因 `index.html` 缺失返回 500。新增 `tests/test_wheel_ships_ui.py` 真实构建 wheel 校验。

## [0.23.2] - 2026-10-12

### Fixed

- 老库 `memoryedge` 补 `reason` 列(迁移 v28,schema 版本 27 → 28)。0.23.1 只补了 `memoryitem.reason`,supersedes / contradicts 边查询在老库上仍报 `no such column: memoryedge.reason`,整段降级为不排序。

## [0.23.1] - 2026-10-11 - 司门补丁

### Fixed

- **老库补 `memoryitem.reason` 列,修复记忆列表 500**:`bea001c1` 给模型加了 `reason` 字段,但迁移链漏写 `ADD COLUMN`,从 0.3.6 升级的老库查询时报 `no such column: memoryitem.reason`,记忆列表接口返回 500。新增 v27 迁移补列(幂等,不动已有数据),并加不 mock 的回归测试。

## [0.23.0] - 2026-10-07 - 司门（Simen · 六道出入查验闸收口）

> 版本代号「司门」：《周礼》地官司徒属官，掌王城门关的开闭与出入稽察，「凡货物出入门关，必司门勘验」。贴合本版主题：六道新闸全是「出入必经查验」——獬豸拦注入入库（入）、勘合防重试重复（入）、循环守卫拦宿主撞墙（入）、并发闸限外呼（出）、复原自查核索引在场（出入核验）、会话分键稽归属（归属稽查）。登记见 [ADR-0013](docs/adr/0013-naming-system.md) §7。

### Added

- **复原路径三层自查——归档复原核查索引在场，缺层补漏如实回报（2026-10-07，票据 `.scratch/restore-three-layer/issues/01-restore-three-layer.md`，上游 aiduMEM f0.3 `2b90cee` restore 漏向量层事故的同族预防）**：
  - **先说影响**：归档设计是「索引保留、复原零成本」，但「保留」是归档侧的承诺——若归档前索引同步本就失败过（当时有告警，用户可能没看），复原后该记忆 active 且**静默只剩单层可搜**：正文明明在库里，向量检索就是找不到，没有任何报错提示。这正是上游 f0.3 修的那类「看似在实则不在」。现在恢复归档时先核查 FTS/向量两层在场（`has` 原语，不做嵌入计算），缺了才补同步（罕见路径才花 embed 钱，常态保持零成本），结果如实进回执：`fts_present/vector_present`（层终态）+ `fts_resynced/vector_resynced`（本次是否补过缺层）。
  - **同族枚举闭环**：全仓把状态翻回 active 的赋值面只有 `record_ops_service` 三处（unretract/unarchive/revive）。unretract 已有完整回执（`fts_synced/vector_synced`）；revive 碎片路径本次补齐同族回执（簇形态不加聚合布尔——多碎片下 `all()` 聚合说不清谁败，失败明细已在 warnings，宁缺不糊）。
  - **裁决**：核查失败与缺层同途（补同步是更直接的二次信号，不给「未知」态留静默通道）；回执语义不与 unretract 撞名（unarchive 没做的操作不得谎称 synced，retract 幂等分支「如实不造假 True」先例）；`has()` 进 VectorStore ABC 抽象面（与 add/search/delete 同列）。
  - **测试**：`tests/test_record_lifecycle.py` 新增 7 例 + `tests/test_bixiao_deterministic.py` 真 Chroma 不 mock 冒烟扩一段（has 原语 + 缺层补漏回执全真链路）。**变异门禁 4/4 KILLED**（FTS 核查谎报恒在场 / 向量核查谎报恒在场 / revive 丢回执键 / 核查异常谎报在场——四个变异各打各的判据，子进程隔离 + bak 快照逐字还原复核）。

- **勘合——潜移异步摄取幂等回执，宿主重试不再重复入库（2026-10-07，票据 `.scratch/kanhe-idempotency/issues/01-idempotency-receipt.md`，上游 aiduMEM f0.3 `2b90cee` 异步幂等同款移植）**：
  - **先说影响**：潜移提交即返回 task_id，但宿主网络重试（超时重发/断线重连）每次都新造 task_id 重复提交——同一段话提纯入库两遍、LLM 白烧一遍。现在提交经幂等键原子抢占：同键同文在回执 TTL 内重放**原 task_id**（响应多 `replayed: true` 标记，宿主当普通 submit 响应轮询原任务即可）；同键不同文 422 拒收；任务失败自动释放键（失败的任务永远不得重放——上游 S-2/S-8 事故教训）；回执表**零正文**（白名单制，本表寿命长于记忆删除动作，正文进回执等于删除后仍可捞）。
  - **两种键两段语义**（对上游的关键分歧）：显式键（宿主传 `idempotency_key`，MCP/REST 均已透传）全语义 done 回执保留 `IDEMPOTENCY_TTL_DAYS`（默认 7 天）跨重启有效；未传键用内容指纹自动键，只护 600s 重试窗、成功不落 done——MCP 入口不透传 session/turn，自动指纹长期保留会把「同一段话隔天再说」误判成重试拒收，重试发生在秒-分钟尺度，长期去重是显式键的职责。
  - **claim 即回执**：抢占与回执同一行——提交前生成的 task_id 随原子 INSERT 落库，重试方在租约窗内拿到的就是原任务号，上游 P1-10 的 SELECT→INSERT 竞态与「抢占到 finalize 之间」的 pending 窗口不存在；过期行条件接管按 created_at 令牌（并发双发现只一个赢，worker 迟到结算找不到自己那条 claim 不覆盖新主）。时间一律 epoch 浮点，避开 sqlmodel aware/naive 跨版本漂移。
  - **fail-open 带告警**：幂等层 DB 故障记 error 后按无幂等放行——去重层故障不挡写入主路；冲突（同键不同文）不 fail-open，如实 422。purge 挂 scheduler 每小时清扫（done 超 TTL / accepted 超租约）。
  - **命名**：按 ADR-0013 登记「勘合」——明代符契剖分、双方勘对相合方为真；重试持同一符契而来，勘合比对原契而应。
  - **测试**：`tests/test_idempotency_receipt.py` 25 例（不 mock 冒烟：真 SQLite + 真线程池 worker + 真 ingest_dialogue）。**变异门禁 5/5 KILLED**（删冲突判定 / 删失败释放 / settle 丢令牌 / 自动键落 done / 回执白名单失守——最后一条首跑存活暴露「只验证白名单键在、没验证正文不在」的测试空洞，补零正文断言后杀掉）。**夹具实证教训**：内存 SQLite + StaticPool 单连接被测试主线程与 worker 线程并发抢，cursor 交错间歇炸 worker → settle 释放键 → 幂等断言随线程时序漂移；改 tmp_path 文件库每 Session 独立连接后 10/10 稳定。

- **獬豸——写入侧注入检测三层闸，注入内容进不了库（2026-10-07，票据 `.scratch/xiezhi-injection-guard/issues/01-injection-guard.md`，上游 aiduMEM f0.3++ injection_guard 检测层同款移植）**：
  - **先说影响**：此前记忆入库对内容不做任何注入面检查——宿主喂进来的网页/文档若埋着「ignore previous instructions」类指令，会原样进 FTS/向量，之后每次召回都把注入文本拼进上下文。樊篱（出口包裹）只能声明「数据不是指令」，拦不住它先入库。现在六个写入漏斗（对话、verbatim 直存、核心记忆、add 家族、异步、纠错）入库前过三层检测：原始特征正则（指令覆盖/角色劫持/ChatML 标记，中英）→ NFKC 归一去标点匹配（粉碎 `i.g.n.o.r.e` / 全角 / 空格分隔变体）→ 重复行轰炸识别（剔结构性重复行后 count≥10 且 ratio>0.6）；命中**整条拒、不改一字**（宁 miss 不脏写，拒绝信息只给层名不给模式全文）。
  - **上游两处真缺口实测抓到并修复**：①归一化层中文模式漏「的」字组（第一层有第二层没有），空格变体第二层接不住；②`disregard all previous instructions` 经典短语两层都接不住（`all` 与 `previous` 同组二选一）。模式表抄的是上游外审修正后的版本（裸 `<system>`/`[section]`/表格分隔行/3 行相同日志全部放行——他们的误拒教训直接变成兰台的误拒红线测试）。
  - **闸位关键裁决（防毒丸死循环）**：add 家族在 coalesce 缓冲**入口**拦、不在冲刷端拦——冲刷 worker 对 ValueError 会 `buffer.requeue` 无限重试，注入载荷若到冲刷端才被拒会变成永久重试批。
  - **命名**：按 ADR-0013 登记「獬豸」——法兽辨曲直、以角触不直者，只指出可疑不代笔改文，与「只检测不改写」同构。
  - **测试**：`tests/test_injection_guard.py` 26 例（不 mock 冒烟：真三层直调 + 真漏斗真库直调，误拒红线夹具锚死）。**变异门禁 4/4 KILLED**（M1 回退外审前宽松判据——首跑存活暴露测试空洞，补「10 行日志 5 行相同」夹具后杀掉 / M2 删 NFKC / M3 摘 dialogue 接线 / M4 删去标点归一）。

- **MCP 工具循环守卫——同一调用反复失败时熔断，宿主不再白撞故障服务（2026-10-07，票据 `.scratch/mcp-loop-guard/issues/01-mcp-loop-guard.md`，上游 aiduMEM f0.3++ `418c3c3` LoopGuard 同款移植）**：
  - **先说影响**：兰台服务故障时（如 LLM 掉线），宿主 Agent 会陷入「调用同一工具→失败→下一轮再调」的死循环——每轮烧 token、每轮同样报错，MCP 上下文被 -32603 垃圾占满。现在同一「工具+参数指纹」在 60s 窗口内失败满 5 次 → 熔断 30s，冷却期内拒绝并带 `retry_after`（宿主得到明确的「别再试」信号）；冷却结束放**单个探针**请求，探针成功即恢复、失败续断；成功调用永远不限流、清零全部状态；在途调用永不取消。
  - **接线**：`lantai/cli/mcp.py` 的 `handle()` tools/call 分支统一收口（60 个 handler 逐个装饰要动全表，一处收口是票 13 教训）。拒绝返回标准 MCP error 帧 `code=-32005`；失败判定 = handler 抛异常（-32602/-32603 照常返回，响应形态零漂移）。参数指纹剥离 `request_id`/`trace_id` 等易变顶层键——换 request_id 重试逃不过熔断；同工具不同参数各自计数不连坐。守卫自身故障时放行（不放大火）。配置 `MCP_LOOP_GUARD_ENABLED/THRESHOLD/WINDOW_S/COOLDOWN_S`，写错值回落默认。
  - **测试**：`tests/test_mcp_loop_guard.py` 10 例（不 mock 冒烟：真 `handle()` 直调 + 真 handler 抛异常 + fake clock 驱动窗口/冷却/单探针）。**变异门禁 3/3 KILLED**（阈值 `>=`→`>` / 探针成功不清熔断 / finish 忽略失败标记；子进程隔离 + 快照逐字还原复核）。
  - **诚实记录**：全量门禁第一轮 8 个失败全部为「门禁进行中并发改源码」的一次性误报（`inspect.getsource` 行号切片错位），干净静止代码下复跑 2029 passed 零失败。

- **注入帧带事件时间——宿主能看到记忆「何时发生」，双轴优先级钉死（2026-10-07，票据 `.scratch/inject-frame-time/issues/01-inject-frame-time.md`，上游 aiduMEM f0.1 同款能力吸收）**：
  - **先说影响**：此前召回注入只有「相关度分数」没有时间——「项目上线」这条记忆不管是一年前还是昨天记的，注入出来一个样，宿主 Agent 无法区分过时信息与新鲜事。现在每条注入条目头部带 `YYYY-MM-DD HH:MM`（可配置粒度），时间取自**事件时间轴**（`event_time`，ADR-0048）优先、回退**主张时间轴**（`valid_from`）——两个轴的语义不同（「现实何时发生」vs「系统何时确信」），调换会静默颠倒注入时间轴的含义。取不到时间就不显示，不硬造。
  - **覆盖四条注入通道**：shell_hook 召回注入、卸载条目、认知上下文（`to_prompt`）、shell_hook 兜底格式化，单一真源 `lantai/core/time.py` `render_event_time()`。粒度 `SHELL_HOOK_INJECT_DATE: day(默认)|minute|off`，写错值回落 day——带不带时间、带到什么粒度是使用者偏好，不是系统的判断。
  - **实施中的真发现（SQLModel 陷阱）**：`valid_from = Field(default_factory=utcnow)` 会在 flush 时**覆盖显式传入的 `None`**——测试里「造 NULL 行」必须物理 raw-UPDATE，内存构造无效。真实库实证：total=731，`valid_from` NULL 仅 31 条（迁移前的 legacy 行），`event_time` NULL 731 条。
  - **测试**：`tests/test_inject_frame_time.py` 13 例（不 mock 冒烟：真引擎种子行 + 真 shell_hook 渲染，含 raw-UPDATE 造 NULL 行）。**变异门禁 2/2 KILLED**（M1 双轴优先级对调 / M2 渲染层丢时间参数；快照逐字还原复核 + `__pycache__` 清理——等长替换+同 mtime 分辨率会让陈旧 pyc 复活，独立教训已记）。

- **潮波缓冲按会话分键——多会话消息不再混并，来源链全链打通（2026-10-07，票据 `.scratch/coalesce-session-key/issues/01-coalesce-session-key.md`，上游 aiduMEM f0.3 同款修复吸收）**：
  - **先说影响**：潮波缓冲键此前是 `user_id:lane` 不含会话——同一个用户的 A、B 两个会话消息会混进同一批一起提炼（上游 f0.3 正是生产上发现「记忆串场」后修的同款）：轻则 A 的话被并进 B 的记忆，重则 A 的私密内容被提炼后挂在无出身的候选上混进检索面。冲刷端还被迫把出身写成空串（「宁 miss 不脏写」的妥协）。现在缓冲按 `user_id:lane:session_id` 分键：只混同会话的消息，出身（session_id）从入队、冲刷、worker 持久化全链透传，两处空串妥协删除。
  - **兼容**：键尾部两段固定为 `lane:session`（user_id 可含冒号，rsplit 解析）；不传 session 的旧调用行为逐字不变；进程热升级窗口里残留的旧格式键也能解析不炸。
  - **变异门禁 2/2 KILLED，其一实为测试空洞补全（诚实记录）**：M2「worker 丢弃 session 出身」首跑存活（6 passed）——查实是测试只断言了键解析、没覆盖 worker 持久化路径；补 `test_worker_persists_session_lineage`（真 worker 直调 + 桩捕获 AddMemoryReq）后 M2 被杀。变异门禁的价值正在于此。
  - **测试**：`tests/test_coalesce_session_key.py` 7 例（不 mock 冒烟：真缓冲、真键、真解析）。全量门禁 2006 passed 零回归。



- **进程级 LLM 并发闸门（2026-10-07，票据 `.scratch/llm-concurrency-gate/issues/01-llm-concurrency-gate.md`，上游 aiduMEM f0.3 同款能力吸收）**：
  - **先说影响**：`chat_json` / `vision_caption` / `embed` 三个外呼通道此前零并发防护——高峰期潮波冲刷、反思定时器、检索意图分类同时触发时，对外部 LLM API 的并发不设防（上游 f0.3 正是生产被打爆后补的同款闸门）。现在三通道共用一个进程级 `BoundedSemaphore`（`LLM_MAX_CONCURRENCY`，默认 4），等位超过 `LLM_ACQUIRE_TIMEOUT`（默认 30s）即抛 `LLMConcurrencyTimeout` 放弃，不排队到死。
  - **三通道同闸**（上游教训：只闸一个通道等于没闸——提取与向量索引会在同一次潮波冲刷里先后触发）；惰性单例按 settings 构造，配置变更自动重建，零硬编码（ADR-0002）。
  - **超出票面的真发现**：tenacity `@retry` 默认重试一切异常——闸门等位超时会被重试 3 次、每次再白等 30s 等位。三处装饰器补 `retry=retry_if_not_exception_type(LLMConcurrencyTimeout)`，超时立即冒泡。
  - **测试**：`tests/test_llm_concurrency_gate.py` 6 例（不 mock 冒烟：真实 Semaphore + 真实函数体 + 本地计数桩网络层）。**变异门禁 4/4 KILLED**（闸门全摘 / 归还句柄失效 / 超时静默降级 / 只摘单通道，子进程隔离 + sha256 还原复核）。

### Fixed

- **FTS 关键词召回不再被空串身份与 admin 形态绕过归属过滤，两处手写收成单一真源（2026-09-29，票据 `.scratch/mcp-identity-gaps/issues/13-search-fts-none-and-empty-identity.md`）**：
  - **先说影响**：`search_fts` / `search_fts_bm25` 的归属块此前是 `build_memories_page` 修前的同族手写形状。实测修前：`user_id=""` 与 `user_id="default"` 在同库同查询下**行为不同**——前者召回 3 条（含 `user-B` 的私有记忆），后者只 2 条。根因是两道判据各自漏半边：`if principal and ...` 与 `if getattr(principal,"user_id",None):` 对空串都为假 → 归属块整个跳过。**空串身份真实可达**：`auth.py:157` `make_principal(api_key.user_id, ...)` 的 `api_key.user_id` 是数据库列，库里一条 ApiKey 的 user_id 为空串即得此形态。顺带补上 **admin 豁免**（票 08 形状在此处漏跟）——`user_id="api_key"` 的真形态此前被收窄成 `user_id=='api_key'`，真实库没这个属主 → 关键词召回对管理员基本空白。
  - **两处手写收到单一真源** `_owner_filter_clause(principal)`：此前 `search_fts` 与 `search_fts_bm25` 各手写一段，票 11 只改到其中一处、差点漏另一处——函数 docstring 里自己写着「改一处漏一处等于没改」。
  - **`principal=None` 刻意不动（本票最重要的发现）**：最初把 None 也一并收敛，**踩到 6 条回归**（`test_fts_owner_recall.py::test_none_principal_unchanged` 两处 + `test_bixiao_deterministic.py` 4 条）。诊断后确认 `None` 在这两个函数的「不过滤」是**票 06 的刻意契约**（docstring 与测试都钉住「worker / 定时反思不能空转」），与票 11 在 `hybrid_search` **入口**收敛 None 是**两个不同层次**的决定：入口收敛保护 MCP 调用方，函数本身保留 worker 契约。按「不动承重墙」红线已回退 None 那半边，**升级为待维护者决策**。补充事实：grep 实证 `lantai/workers/` 不 import `search_fts`，该契约目前在仓内无真实调用方，但契约存在本身是刻意的且 bixiao 测试依赖它。
  - **测试**：`tests/test_fts_none_empty_identity.py` 11 例，不 mock 冒烟（真 FTS5 trigram + 真 MemoryItem + 真 `sync_fts`）。**SYSTEM_VIEWER 用 `role="user"` 单独钉**——`Principal.is_admin` 是 `role in ("admin","system")`，`role="system"` 天然就是 admin，用它测会走 admin 分支、压根碰不到 `_is_system_viewer` 分支（与票 11 踩过的坑同源）。
  - **变异门禁 6/6 有效 KILLED，4 个预期冗余如实记录**（10 个变异体，子进程隔离 + 还原后 sha256 复核）。发现**两组判据重合，都是纵深防御而非缺陷**：① SYSTEM_VIEWER 有两层（调用点守卫 + helper 分支），删任一层另一层兜底，同时删两层才红（M7 KILLED）；② `principal=None` 的「不过滤」也有两层（调用点 `principal is not None` + helper None 分支），同时删两层才红（M10 KILLED）。**4 个存活全部是预期重合，没有一个是真的测试空洞**——最初报 MISSED 时以为测试漏了，补测试后才发现是判据重合；补的那条测试本身有价值（排除「靠 admin 分支顺带通过」的假绿）。

- **记忆关系星图 `build_graph` 无身份时不再露出别人的记忆节点（2026-09-29，票据 `.scratch/mcp-identity-gaps/issues/12-ops-graph-missing-or-is-null.md`）**：
  - **先说影响**：`lantai/ops/graph.py` 的 `build_graph`（MCP `graph_view` 与 REST 共用）是 `build_memories_page` **修前**的同族形状——`if principal:` 块内只判 `user_id` 非空就加归属条件，**没有 `is_admin` 豁免、没有 `OR IS NULL`、没有 None 收敛**。票 03 / 07 / 08 三次修正都没跟上这里，三个方向全中：① `principal=None`（MCP 宿主不透传 `user_id`）→ 一个条件都不加 → **星图露出别人的记忆节点**（实测 None 视角 nodes=2 含 user-B）；② 显式 user → 只留自己的行，跨用户边另一端不在池 → 边整个被丢 → **自己的图也空白**；③ admin（`user_id="api_key"`，`auth.py:168` 的真形态）→ 被收窄到 `user_id=='api_key'`，真实库没这个属主 → **星图对管理员空白**。
  - **修法与仓内多数派逐字一致**（`build_memories_page`、`get_core_memory`、`find_duplicate_verbatim`、`hybrid_search` 均如此），不新造第四种口径：None → 收敛 `viewer_of(None)` + `OR IS NULL`；admin → 一律不加归属过滤；显式 user → `viewer_of(principal)` + `OR IS NULL`。
  - **边侧无越权暴露（实证后决定不加过滤）**：`probe_12b_edge_ownership.py` 四个视角的 links 端点全部落在自己的 nodes 内——`edges` 查询按 `mem_ids` 取池内边，池已按归属收窄，端点必然在池内。**不需要给边加归属过滤**，省一处无效改动。但探针暴露两个非安全语义后果并已记录：user-A 自己建的跨用户边连 A 自己都看不到；只有跨用户边的记忆，其图对该用户完全空白。
  - **测试**：`tests/test_graph_principal_scope.py` 6 例，不 mock 冒烟（真内存 SQLite + 真 FTS5 + 真 MemoryItem + 真 MemoryEdge 直调）。**种子设计上一个必要细节**：`build_graph` 节点入选条件是「参与入选边任一端，或携带 scene_id」，只给 `m-A` 连跨用户边则边另一端出池 → 边丢 → `m-A` 无 scene_id → 节点不入选，四种形态全返回空图，**会误判成「没有差异」**。故额外 seed 一条池内边。
  - **变异门禁 4/4 KILLED**：归属块整个删掉 / None 收敛删掉 / 删 `OR IS NULL` 半边 / admin 豁免失效。**差集诚实记录**：M1（删整个归属块）与 M3（删 `OR IS NULL`）杀的测试集完全重合——M1 是**组合变异体**，同时废掉两个分支，而这两者在显式 user 视角上效果相同。M1 的独立价值不是差集，而是证明「两个分支都必须存在」。为 M2 补了 None 视角的精确集合断言后它与 M1 拉开（1→2 条）。**不伪造分离。**

- **admin 两种构造形态在 VAULT 档案页上看到的数据不再差一整套（2026-09-29，票据 `.scratch/mcp-identity-gaps/issues/08-admin-two-forms-inconsistent.md`）**：
  - **先说影响**：同一个管理员，`Principal(user_id="api_key", role="admin")`（`auth.py:168` HTTP 环境变量 API key 的**真形态**）与 `Principal(user_id=None, role="admin")`（测试/CLI 常写法）在 `build_memories_page` 上看到的数据**差一整套**。实测修前：前者 `total=1`（只剩 NULL 老行）、后者 `total=3`。**票面原判还说轻了**——`viewer_of` 对 `"api_key"` 原样返回（≠ `"default"`），于是管理员连自己 `default` 属主的记忆都看不到，真实库上**档案页基本空白**。方向是"该看的没看到"（运维排障误导，排查"这条记忆去哪了"会得错误结论），不是越权泄漏。
  - **修法**（与仓内既有设计一致，非新造特例）：`if principal:` 块内加 `is_admin` 判定，admin 一律不加 user 归属过滤。同文件**四处**已是这个形状（`_kaogong_scope` :73、`get_core_memory` :498、`put_core_memory` :523、`find_duplicate_verbatim` :640），本处是唯一漏跟的——它靠在 `user_id` 上判空"意外"放过了 `user_id=None` 的 admin。
  - **只统一 `user_id` 这一处**（两种形态差异的唯一来源）：`tenant` / `session` / `agent` / `allowed_lanes` 是调用方显式传的收窄条件，不属身份差异；顺带放开会把「admin 全表」扩大成「admin 无条件」，那是另一个决定，不夹带。**不动 `viewer_of`**（仓内唯一收敛真源，在调用点判）。
  - **测试**：`tests/test_memories_page_admin_two_forms.py` 6 例，不 mock 冒烟（真内存 SQLite + 真 FTS5 + 真 `MemoryItem` 行）。护栏三条：显式 user 收窄逐字不变、`principal=None` 的票 07 收敛口径不互踩、票 03 的 `OR IS NULL` 半边不丢。
  - **变异门禁 4/4 KILLED 且差集两两不重合**（2/7/5/5）：admin 分支整个关掉 / 反转判据 / **豁免过度扩大**（`is_admin` 恒真——杀的是显式 user 的 5 条，与 M1 不重合；原写的 `not False` 与 M1 判据完全重合，已替换）/ 删 `OR IS NULL` 半边。
  - **本轮一个自律**：变异探针最初放了一个 old == new 的"占位变异体"，靠 docstring 解释为何跳过——这是假动作，会让"N 个变异体"看起来比实证过的多。已删除，反方向改写为独立探针真跑。

- **主检索路径 `hybrid_search` 无身份时不再召回别人的私有记忆，同时补出 `SYSTEM_VIEWER` 在检索三通道里的三个洞（2026-09-28，票据 `.scratch/mcp-identity-gaps/issues/11-hybrid-search-none-unfiltered.md`）**：
  - **先说影响**：`hybrid_search` 是**每一次提问都会走的主干路径**（MCP `search` 与 `verbatim_search` 都走它）。`principal=None` 时**三条召回通道 + 最终合并步一层归属过滤都没有**——别人的私有记忆直接进最终结果。前三处（07 `mem_recent` / 09 `scene` / 10 `offload`）都是旁路，这一处是主干。同族形状、判据各不相同（`if principal:` / `vector_owner_filter` 的 None 分支 / `_query_items` 压根没有条件）。
  - **两个 commit 分开提交**（按 01b 教训）：**A. 接线**——`handle_search` 把已算出的 `principal` 传给 `hybrid_search`（它在 `:52` 算出来却只用在检索事件日志里，调用一个身份参数都没有；这是比 None 语义更基础的漏）。**B. None 收敛**——入口把 `None` 收敛成 `Principal(user_id="default")`（经 `acl.viewer_of` 单一真源），三通道的 `if principal:` 收敛后恒真、**自动生效，不需要逐通道改**，将来新增通道也自动被覆盖。
  - **不动 `vector_owner_filter` 的 None 分支**（判据同 10 号票：不动承重墙，在调用方收敛）——它的 docstring 明写"admin / principal=None → None（不过滤，worker/CLI 不能空转）"，那是 15 号票**刻意**写的通用契约；worker 直调它仍全表，MCP 路径被收窄。
  - **`_query_items` 补最后一道**，且**它不是冗余防御**（差集实证见下）：`vector_owner_filter` 的 `{"user_id":""}` 对**所有**用户生效（票 15 契约：NULL 属主在 Chroma 里落成空串），而 `_query_items` 只认 NULL 不认空串——"DB 有属主、向量 metadata 还是空串"的记忆，**只有这一道拦得住**。`index_memory_item` 的 metadata 是调用方手搓的，391 行 verbatim 回填（`fts-null-owner/02`，待维护者确认）之后 DB 属主被改写、向量 metadata 仍是空串，差集会从理论变成生产大面积。
  - **修法之外查出三个真洞，全部是 `SYSTEM_VIEWER` 的"逐函数手写"漏网**：SQL 侧六个 service 有全表口径，**向量通道与 FTS 两条没有**——"显式系统身份"被当成普通用户 `"__system__"` 过滤，真实库没有这个属主的行，**全量批处理被误滤成空集**（不是"多看到"，是"什么都看不到"）。06 号票立项时只改了六个 service，本票是第一个让 worker 语义的身份走到 `hybrid_search` 的改动，才把它暴露出来。修 `acl.vector_owner_filter` + `fts.py` 两处（新增本地 `_is_system_viewer`，不 import `acl`：本模块被只装检索依赖的环境引用）。
  - **影响面盘点先行**（AST 扫 14 个文件有裸调用，5 个种子属主非 default 需补 `principal=`），逐个显式处理而非等它们静默 break；eval 三处改传 `Principal(user_id=SYSTEM_VIEWER)`——**给显式身份，而不是放宽收敛**（eval 的向量 metadata 根本没有 `user_id` 键，Chroma 里"键不存在"不等于"空串"）。
  - **测试**：`tests/test_hybrid_none_principal.py` 10 例，不 mock 冒烟（真内存 SQLite + 真 FTS5 trigram + 真 `MemoryItem` 行；向量通道替身只换外部存储层，filter 判定与合并逻辑仍是真代码）。**变异验证 4/4 全杀**（`probe_mutation_hybrid_none.py`，子进程隔离 + 还原后复核）：入口收敛 / `_query_items` 归属 / 向量通道 SYSTEM_VIEWER / FTS+BM25 SYSTEM_VIEWER。全量 **1970 passed / 0 failed**（基线 1960）。
  - **两个值得单独记住的教训**：① **`role="system"` 天然就是 admin**（`Principal.is_admin` 是 `role in ("admin","system")`）——我给 `SYSTEM_VIEWER` 写的三条测试想当然用了 `role="system"`，于是它们走 admin 分支、**压根没碰到要钉的那条分支**，变异体因此存活。没有变异探针，这三条会一直绿着而我以为它们在保护 SYSTEM_VIEWER。② **探针不报红也可能在骗你**：差集探针里忘 patch `embed`，向量步静默失败回落关键词兜底，两个场景都返回 `[]`，看起来正是"`_query_items` 拦住了"的预期结论。

- **MCP `offload_read` 无身份时不再读别人的卸载全文——`ensure_can_delete(None)` 是空操作这一层被堵住（2026-09-28，票据 `.scratch/mcp-identity-gaps/issues/10-offload-read-none-unfiltered.md`）**：
  - **先说影响**：`read_offload_file` 最后一行是 `path.read_text()`——**别人的记忆全文，不限长度、不走 recall budget**。而归属校验整段挂在 `if principal is not None:` 下，宿主不透传 `user_id` 时 `principal=None`，**校验一次都不跑**。这是同族漏洞的第三处（07 `mem_recent`、09 `scene`、本票 `offload_read`），**三处判据各不相同**（`if principal:` / `principal is not None and not is_admin` / `if principal is not None:`），失效方式却相同：None 让整段归属逻辑不可达。
  - **根因最该记住的一条：`ensure_can_delete(None, ...)` 是彻底的空操作**（探针 S0 实证：传 `resource_user_id='user-B'` 也不抛错）。它的每个守卫都要求 principal 的某个字段非空——`getattr(principal,"is_admin",False)` → False 不放行；`p_user = getattr(principal,"user_id",None)` → None，于是 `resource_user_id and p_user` 恒假。**"调用了 `ensure_can_delete`"不等于"校验过了"**。已由 `test_ensure_can_delete_none_is_noop` 单独钉住这个反直觉事实。
  - **修法同票 02 给 `_ensure_can_decide` 的形状：收敛 principal 本身，不给 `ensure_can_delete` 加形参**（后者被 27 处写侧共用，是承重墙）。admin 早退必须在收敛之前——admin 的真实形态正是 `user_id=None`（`auth.py:168`），`viewer_of` 会把它变成 `"default"`；靠 `role` 传的 `is_admin` 不受影响，但把"admin 不校验"写在函数开头是**可读性选择**（同票 02）。
  - **为什么这个形状而不是 07/09 的 `not is_admin`**：07/09 下游是 `viewer_of` 收敛 + `OR IS NULL` 的读侧口径（NULL 老行可见）；本票下游是 `ensure_can_delete`，它的既定语义是"资源标了 user_id 且与主体不同 → 403；**资源无归属 → 不视为越权**"——NULL 老行**天然放行**，所以不需要 `OR IS NULL`，只需要让 `p_user` 非空。**判据仍是"下游有没有现成的收敛"，只是收敛的落点不同。**
  - **单人部署不空转**（正向检查）：真实库 636/657 行 `memoryitem` 是 NULL 属主，`ensure_can_delete` 对"资源无归属"既定就是放行，探针 S2/S6 双验 NULL 老行与 `default` 自己的行无身份仍可读。错误形态是 `HTTPException(403)`（不是 404）——**区分"存在但不是你的"与"不存在"是对的**，与探针 S3（带身份读 B）同口径。
  - **两条既有测试显式依赖 None 绕过，本批逐条给依据后改写**：`test_offload.py::test_write_read_roundtrip` 与 `test_mcp_offload_read_tool` 原本都不传身份、库里也不种 `MemoryItem` 行——**它们能过恰恰是因为那个洞**（"孤儿文件 + 无身份"本该按 docstring 里早写着的"记忆不存在/无归属一律不放行"处理，只是对 None 从未可达）。改写方式是给显式身份 + 种对应属主的行，**覆盖一字不减**（同一 handler 真入口、同样断言返回全文与 id、同样验缺参 -32602）。依据链：01b 号票的验收口径只写了"A 读 B 的 memory_id → 拒绝"（显式身份），**没有 None 那一半**——与 07 号票同一种"半修被记成已修"。**教训：验收口径必须同时写"带身份"与"无身份"两种形态。**
  - **测试增量 6 例**（`tests/test_offload_none_principal.py`，全部不 mock：真 tmp_path 落盘 + 真内存 SQLite 真建表 + 真 `MemoryItem` 行，直调 `read_offload_file`）。**Red 实证 1 failed / 5 passed**——红的正是洞那条（`DID NOT RAISE`），五个是护栏（含"单人部署不空转"与"admin 仍全权"）。
  - **变异验证 4 KILLED / 0 MISSED**（`.scratch/mcp-identity-gaps/mutation_check_10.py`）：V1 退回修前（None 不校验）、V2 整个校验消失、V3 收敛到别的字符串、V4 算了 `viewer` 却不用（条件改永假）。还原后逐字节一致。
  - **全量 pytest 1957 passed / 0 failed**；ruff check + format 均过（中途被 `test_lint_gate_passes_on_real_repo` 抓到一次 import 顺序 + 一处格式——**回归哨兵第四次当场干活**）。

- **MCP `scene_get` / `scenes_list` 无身份时不再全表——B 的场景摘要与成员全文守住了（2026-09-28，票据 `.scratch/mcp-identity-gaps/issues/09-scene-tools-none-unfiltered.md`）**：
  - **先说影响**：`MemoryScene` **没有归属列**（`tables.py:174`——场景是聚类产物），归属只能按**成员记忆**反查。而 `get_scene` / `list_scenes` 的判据都是 `principal is not None and not is_admin`——宿主不透传 `user_id` 时 `principal=None`，**整段归属校验跳过**：`get_scene` 返回 B 的场景 `summary`（LLM 依据成员内容生成的摘要）**和全部成员的完整正文**；`list_scenes` 把 B 的场景连摘要一起列出来——A 刷列表就读到 B 的场景主题画像。与票 07（`mem_recent`）**同一个形状**：窄化逻辑本身是对的，问题只在 `None` 那一侧。
  - **决定性实证**（`.scratch/mcp-identity-gaps/probe_scene_none_semantics.py`，8 场景，ZZ 标记认行）：S1 None 下钻纯 B 簇 → B 的 summary + 成员全文（洞）；S2 None 列场景 → 含 sc-B 与 B 的 summary（洞）；S6 None 下钻混合簇 → `m-mix-A` + `m-mix-B`（洞，**最要害**）。**四个护栏修前修后全绿**（S3/S5 带身份收窄正确、S4 列表正确、S7 admin 仍全权），证明改动只动了 None 这一侧。
  - **修法两处判据，`not is_admin` 即可，没有新写收敛逻辑**：`getattr(None, "is_admin", False)` 返回 `False`，所以 None 照样进收窄分支，再由 `_scene_visible_member_ids` 里**已有的** `viewer_of(principal)` 收敛到 `"default"`。**这是与票 07 的形状差异（方法论，值得记）**：07 是 `if principal is None:` 单独 append 一条 cond，因为那里的归属块整个挂在 `if principal:` 里、下游没有收敛点；本票改成 `not is_admin` 即可，因为下游已经调了 `viewer_of`。**判据是"下游有没有现成的收敛"，不是照搬上一票的代码形状。**
  - **单人部署不空转**（正向检查，不是推算）：真实库 `memoryitem` 657 行 = NULL 636 + `default` 21，收敛后 NULL 老行与 `default` 自己的场景仍可见（探针 S8 + 测试 `test_single_user_deployment_not_broken` 双验）。**"为了安全把场景列表清空"会是另一个 bug**，所以这条单独钉住。
  - **顺带修正一处过期 docstring**：`get_scene` 原文写"混了 B 的成员的簇仍会把 B 的正文带出来"——那是成员过滤（现 :288 行）补上**之前**的说法，探针 S5 实测 A 只见 `m-mix-A`。**docstring 过期比没有 docstring 更危险**，它会让人照着已经不存在的行为做判断（本票第一版就读了它，据此写了"混合簇会漏"的预期，被探针打脸后才回去读了代码）。已在同一次提交改正。
  - **本票来自研究批的 AST 清点，不是遗留清单**：票 02 原文"空结果那 17 个工具本轮探针无效……它们的 None 语义仍未知"。清点方法：判据只有一条且可实证——"函数里有没有一处按 principal 建的 where 条件"，用 AST 把 32 个读工具分成 A/B 两类，再对 A 类的人读代码。**静态分析只负责缩小范围，定性仍靠人**（AST 一度把 `get_scratchpad` 标成"裸 principal 解引用"，人读后发现 05 号票早修了，`_owner_of` 才是 None 的处理点）。同一轮还确认 `run_autodream_once` / `collect_usage` / `read_wiki_page` / `get_task_status` / `detect_memory_probes` **不是洞**（理由逐条记在票里）。
  - **探针自己错两次（01b 教训第四次）**：① 只覆写 `settings` 没覆写 `db_module.engine`，而 `scene_service` 用 `db.get_session()`——种子进隔离库、查询读宿主真实库，表现为**场景凭空 404**。我一度据此判"代码有洞"，还把那句过期 docstring 当依据。**判"代码有洞"之前，先确认探针读写的是同一个库。** ② 加了 `db_module.engine = E` 后诊断块仍查不出 mix 成员——诊断块自己的 session 与 seed 的 session 混用。最终形状：seed 一个 session 一次 commit，查询各自开新 session，不共享。
  - **测试增量 6 例**（`tests/test_scene_none_principal.py`，全部不 mock：真内存 SQLite 真建表，`_run()` 把 `db_module.engine` 指到被测临时库后直调 `get_scene` / `list_scenes`）。**Red 实证（git stash 对照）3 failed / 3 passed**——红的正是三个洞，绿的三个是护栏（显式用户仍收窄、admin 仍全权、单人部署不空转），证明它们是护栏不是新断言。
  - **变异验证 5 KILLED / 0 MISSED**（`.scratch/mcp-identity-gaps/mutation_check_09.py`）：V1 `get_scene` 退回修前（None 不校验）、V2 `get_scene` 整个校验消失（B 的全文露出）、V3 `list_scenes` 退回修前、V4 `list_scenes` 完全不做 scope、V5 收敛到别的字符串（形似而实非）。还原后逐字节一致。
  - **全量 pytest 1951 passed / 0 failed**；ruff check + format 均过（中途被 `test_lint_gate_passes_on_real_repo` 抓到一次新测试文件格式，已修——**回归哨兵第三次当场干活**）。

- **MCP `mem_recent` 无身份时不再全表返回——宿主不透传 `user_id` 也能守住归属（2026-09-28，票据 `.scratch/mcp-identity-gaps/issues/07-mem-recent-none-full-table.md`）**：
  - **先说影响**：这不是新引入的，是 02 号票清点出、一直没修的那一半。`build_memories_page`（`lantai/services/memory_service.py`）的整个归属块挂在 `if principal:` 下——宿主不透传 `user_id` 时 `principal=None`，**五条归属条件一个都不加**，全表返回，含别人的记忆正文。同文件里那段精心写的 `viewer_of` 收敛 + `OR IS NULL`，在 `None` 时**整段不可达**。实测（`probe_02c_three_way.py`）：`mem_recent` 不带身份 → 4 条（含 B 的私有记忆）；带 `user_id` → 各只见自己 + NULL 老行。01a 票修的是前半（带身份时收窄），**本票补后半**。
  - **修法：只给 `principal is None` 补一条收敛条件，显式身份与 admin 逐字不动**。收敛到 `"default"`（经仓内唯一真源 `acl.viewer_of`），不是"一律拒"（那会让普遍不传 user_id 的 MCP 客户端全部空转），也不是"保持全表"（那就是洞本身）。**为什么这个形状而不是票面原判的"把 `viewer_of` 提到 `if` 外面"**：后者会让显式身份与 None 走同一条、与既有 user 分支重复 append 等价条件；单独一条让"None 与非 None 是两个分支"在代码上一眼可见，与同文件 `get_core_memory`/:503、`put_core_memory`/:532、`find_duplicate_verbatim`/:644 三处的 `if not is_admin:` 分支风格一致。**tenant / session / agent / allowed_lanes 四条保持挂在 `if principal:` 下**（票面原判断，正确）：它们没有"无身份回落值"可言，None 时加上反而滤空。
  - **单人部署零影响**（真实库实测，不是推算）：`memoryitem` 657 行 = NULL 636 + `default` 21，**除 `default` 外没有任何别的属主**。收敛后看到的数据与全表**逐行相同**。同批落地探针逐条验过：`default` 自己的行在、636 条 NULL 老行在、别人的 id 与正文都不在。
  - **三条既有测试显式断言"None = 全表"，本批逐条给依据后改写**（这不是"改测试让代码过"，依据链完整记录在票里）：它们建于 03 号票，而 03 号票自己的验收记录写的是"02 号票『读操作不收紧』的回归护栏"——**守的是 02 号票的一条全局价值观，不是为 `build_memories_page` 单独论证过 None 该不该全表**；而 02 号票随后的清点**推翻了自己的那条全局价值观**（20 个读工具里 `mem_recent` 是唯一实证的洞，票面定的是"改收敛口径"而非收紧/放开）。改写后断言从"能看全部"改成"收敛生效"，**没有删掉任何覆盖**；`test_admin_unchanged`（admin 另一形态）**一条都没动**，它仍锁着"admin 全表"。
  - **反向确认过没有"内部 worker 需要 None 全表"**：全仓 grep `list_memories` / `build_memories_page` 的真实调用方只有三个——`routes_memory.py:86`（HTTP，`ctx` 永不为 None）、`routes_terminal.py:171`（HTTP，同上）、`mcp.py:601`（MCP，可能 None）。**没有任何 worker / CLI / eval 调它**，"None = 全表是给内部 worker 留的口子"这个说法没有调用方支撑。
  - **测试增量 4 例**（`tests/test_memories_page_none_principal.py`，全部不 mock：真内存 SQLite + 真 FTS5 trigram，直调 `build_memories_page`）。**Red 实证 1 failed / 3 passed**——红的正是洞那条（返回 `['m-A','m-B','m-def','m-legacy']`），三个护栏先绿，证明它们是护栏不是新断言。
  - **变异验证 4 KILLED / 0 MISSED**（`.scratch/mcp-identity-gaps/mutation_check_02b.py`，subprocess 隔离 + atexit 还原 + 归一化换行指纹复核）：V1 退回修前（None 不加条件）、V2 去掉 `OR IS NULL` 半边（老行消失、单人部署空转）、V3 收敛到别的字符串、V4 改成"排除 B"而非收敛。还原后逐字节一致。
  - **落地探针自己错了一次（01b 教训第三次）**：`probe_02f_mem_recent_landing.py` 走 `cli/mcp.py::handle_mem_recent` **真入口**（不走 service），第一版在"admin（`user_id='api_key'`）"场景断言见全部 5 条、实测只得 2 条 → 判 ❌。**没有直接采信**，用 `git stash` 撤掉修法重跑同一条探针：**修前修后都是 2 条**——是我的预期错，不是代码回归。真相：admin 的 `user_id` 是 `'api_key'`（非空），落进 user 分支被收窄成 `user_id=='api_key' OR IS NULL`，只剩 NULL 老行。"admin 全表"只对 `user_id=None` 那种形态成立。探针已改为两个场景分别锁住两种形态。
  - **顺带发现 admin 两种形态行为不一致，本批不夹带**（`user_id='api_key'` 被收窄、`user_id=None` 全表）——方向是"该看的没看到"（运维排障时 VAULT 档案页给管理员一个错误的库容印象），不是泄漏，故 P3，已另立 `.scratch/mcp-identity-gaps/issues/08-admin-two-forms-inconsistent.md`。按 01b 教训**分开提交、分开验证**。
  - **全量 pytest 1945 passed / 0 failed**；ruff check + format 均过。

- **MCP `proposal_decide` 不再越权改别人的提案——宿主不透传 `user_id` 时归属校验照常生效（2026-09-28，票据 `.scratch/mcp-identity-gaps/issues/02-mcp-identity-contract.md`）**：
  - **先说影响**：这是**真脏写**。MCP `proposal_decide` 在宿主不透传 `user_id` 时拿到 `principal=None`，而 `evolution_service._ensure_can_decide` 的第一行是 `if principal is None: return`——**归属校验整段跳过**。决定性实证（`.scratch/mcp-identity-gaps/probe_02e_decide_proposal.py`，真 `decide_proposal` + 内存 SQLite 真建表）：`principal=None` reject B 的提案 → pending **变成 rejected**；`principal=user-A` 同样操作 → 403、状态不变。**同一个函数，带身份就拦，不带身份就放行。**
  - **`approve` 比 `reject` 更重，而它同样越权**：`approve` 会 `apply_proposal` 直接写库（把待决内容变成正式记忆）。实测 `principal=None` approve B 的提案：下游 `apply_proposal` **确实**按归属挡住了内容写入并返回 `{"ok": false, "reason": "target not owned"}`（宁 miss 不脏写），**但提案状态已经从 pending 变成 rejected**——`decide_proposal` 先改状态再 commit，下游失败挽不回。**"下游有校验"不能替代"上游先校验"**，这是本票最该被记住的一条。
  - **HTTP 侧不受影响**：grep 实证 `decide_proposal` 全仓 3 个调用方，`routes_evolution.py:62` 与 `routes_work_items.py:65` 都显式传 `ctx`（`get_current_user` 永不返回 None）。**这个洞只有 MCP 入口能触发**——第二个入口面的典型代价，同 04/05 号票。
  - **修法一行，复用既有收敛真源**：`_ensure_can_decide` 的 None 口径从"不校验"改成经 `acl.viewer_of(principal)` 收敛（None → `"default"`）后照常校验。**收敛的是 principal 本身，不是给 `ensure_can_delete` 加形参**——后者是被 27 处写侧共用的承重墙，本票不动。只在 `viewer != principal.user_id` 时构造新 principal，已有身份的走原对象，现有行为逐字不变。
  - **单人部署不空转**：真实库唯一非空属主就是 `default`，收敛后照样能裁决自己的全部历史提案（探针 S4 实测：`principal=None` reject `default` 的提案仍成功）。
  - **admin 早退必须留在收敛之前**：admin 的真实形态正是 `user_id=None`，收敛会把它变成 `"default"`；但 `role` 保留、`is_admin` 仍在，行为不变。保留早退是**可读性选择**（"admin 不校验"写在函数开头，下一个人不用推导 `ensure_can_delete` 内部才敢改这里），不是行为选择。
  - **测试增量 5 例**（`tests/test_decide_proposal_none_principal.py`，全部不 mock：真 `decide_proposal` + 真 `ensure_can_delete` + 内存 SQLite 真建表 + 真 FTS）。**Red 实证 2 failed / 3 passed**——红的正是 reject / approve 两条越权路径，绿的三个是护栏（default 仍可裁、显式用户仍被挡、admin 仍全权）。
  - **变异验证 4 KILLED / 1 等价变异（诚实记录）**（`.scratch/mcp-identity-gaps/mutation_check_02.py`，subprocess 隔离 + atexit 还原 + 归一化换行后指纹复核）：V1 退回修前（None 不校验）、V2 整个校验消失、V4 算了 `viewer_of` 却不用、V5 改成硬闸（None 即拒，会把单人部署的 default 提案也挡掉）——四个全杀。**V3（去掉 admin 早退）存活，逐条实证后确认是真等价**：admin 靠 `role` 传 `is_admin`，`viewer_of` 只改 `user_id` 不动 `role`，而 `ensure_can_delete` 自己第一行就判 `is_admin`——实测两者对 B 的资源都放行。未粉饰为"被杀"。
  - **同票的读侧洞 `mem_recent` 已由 07 号票修完**（见本 Changelog 上一条）：根因 `memory_service.py` 的 `if principal:` 让 `viewer_of` 收敛整段不可达，宿主不透传 `user_id` 时全表返回含 B 的正文。写侧（本票）与读侧（07 号票）是两处独立改动，读侧回归面（HTTP VAULT 页 + MCP + 可能的 worker）比写侧大，按 01b 教训**分开提交、分开验证**。
  - **清点方法论（本票最大的副产品）**：`principal=None` 的语义**必须逐函数实测，不能一刀切**。本轮三个探针、两轮自我纠正才得到可信结论：第一版探针没隔离数据目录（读写宿主真实库 + chromadb 日志冲掉 print）；第二版把 `legacy` 露出也当成泄漏，而 legacy 露出正是 `OR IS NULL` 的既定口径，于是把 `mem_recent` 误标成"带身份也没收窄"——与 01a 票的结论直接矛盾，**按 01b 教训先核实再记票**才发现收窄是好的、问题只在 None 那一侧。**判定不可信时先怀疑探针。**
  - **全量 pytest 1940 passed / 0 failed**；ruff check + format 均过（中途被 `test_lint_gate_passes_on_real_repo` 抓到一次新测试文件格式，已修）。


- **关键词召回补 `OR IS NULL`——单人部署下不再丢掉 96.8% 的记忆（2026-09-28，票据 `.scratch/fts-null-owner/issues/01-fts-missing-or-is-null.md`）**：
  - **先说影响**：这是**功能大面积失效**，不是"收紧了点"。`search_fts` / `search_fts_bm25` 的归属条件是裸的 `AND m.user_id = ?`，没有 `OR IS NULL` 半边。真实库 657 行 `memoryitem` 里 **636 行是 NULL 属主（96.8%）**——归属列是后来才加的，老数据全是 NULL。于是带 principal 的关键词召回**只能看到 21 行**，且**越老的记忆越搜不到**。用户感知是"时灵时不灵"而不是"搜不到"，极难排查。实测口径对比：口径 A `user_id=? OR IS NULL` 657 行，口径 B（FTS 现行 SQL）21 行。
  - **同族修法的第三次，前两次都在别的通道**：`build_memories_page`（VAULT 档案页）、向量通道（`$or[viewer, ""]`——空串是 NULL 属主在 Chroma 里的落点）都补过。读侧每条收窄的统一口径是 `user_id == viewer OR user_id IS NULL`，**FTS 这两处是漏网的**。
  - **为什么错了一年没人发现**：`git grep -rn -E "search_fts|search_fts_bm25" -- tests/` 共 20 处调用，**没有一处传 `principal`**。带 `principal` 的 SQL 分支从写下来就没被执行过——这是 mock/测试覆盖的盲区，不是逻辑难。
  - **NULL 是「未记录」不是「属于所有人」**，但必须可见：`test_other_users_row_still_invisible` 同时钉住反方向，B 的行对 A 仍不可见。**放行的是未记录，不是放开隔离。**
  - **顺带修了 07 号票一处失效断言**：`test_verbatim_search_ownership.py::TestNullOwnerTradeoff::test_null_owner_row_invisible_to_non_admin` 断言"NULL 属主行对非 admin 不可见"——与本票直接冲突。决定性实验（`.scratch/fts-null-owner/probe_decisive.py`：种一条 NULL 属主记忆同时进 FTS 与向量库，`principal=default` 跑完整 `hybrid_search`）证明该断言**从未隔离过 NULL 行**：`_query_items`（`hybrid.py:534`）无任何归属过滤，只要任一通道放行就进最终结果。改为两个方向都锁（NULL 可见 + 别人的行不可见），并在类 docstring 记下废止理由。**教训：断言红时先问"这条断言保护的是哪个洞"，别默认是自己改坏了。**
  - **测试增量 8 例**（`tests/test_fts_owner_recall.py`，全部不 mock：真内存 SQLite + 真 FTS5 trigram，`_seed` 走真实 `sync_fts`）。含 1 条端到端（关掉向量通道孤立验证关键词通道——否则向量召回会把结果补回来，掩盖 FTS 的漏）。**Red 实证**：4 failed / 4 passed。
  - **变异验证 5 KILLED / 0 MISSED**（`.scratch/fts-null-owner/mutation_check_01.py`，subprocess 隔离 + 内容指纹还原 + 还原后复核）：V1 删 FTS 的 `OR IS NULL`、V2 删 BM25 的、V3 两处都删、V4 整个归属过滤不要、V5 反转成 `AND user_id IS NULL`。
  - **踩坑记录**：① 变异目标定位——两个修点的 SQL 字符串完全相同，靠**尾部 ORDER BY 子句**区分（`search_fts` 按 rank、`search_fts_bm25` 按 score）。② `Path.write_text` 在 Windows 写 CRLF，字节哈希还原比对会误报"还原失败"——归一化换行再比。③ 探针里我一度写"`search_fts` 有重复的 `AND m.lane IN` 拼接"，读 `fts.py:148-153` 证明**只出现一次**，是我的 grep 输出看串行，已在票里更正。**判定不可信时先怀疑自己的探针。**
  - **全量 pytest 1933 passed / 0 failed**；`ruff check` + `ruff format --check` 均过。

- **LIKE 兜底补 `OR IS NULL`——短词查询下老记忆不再被最后一层通道单独抹掉（2026-09-28，票据 `.scratch/fts-null-owner/issues/03-keyword-fallback-like-missing-or-is-null.md`）**：
  - **先说影响**：LIKE 是关键词召回的**最后一层**（向量挂了走 FTS，FTS 挂了只剩它）。它的触发条件是 `(not candidate_ids or has_short_tokens)`，其中 **`has_short_tokens` 是日常路径**——中文单字词、缩写、型号（"3080"、"RX"）都 <3 字符，trigram 成不了词。此时 FTS 已召回 NULL 属主老行、`candidate_ids` 非空，但 LIKE **另起一条严格 SQL**，把老行从它自己那半边剔掉。探针实证（`.scratch/fts-null-owner/probe_like_fallback.py`，查询词「华硕」2 字符）：三条记忆（NULL 老行 / 别人的行 / 自己的行）只剩自己的那条。
  - **与 01 号票不是重复**：01 修 `storage/fts.py` 两处原生 SQL，本票修 `retrieval/hybrid.py` 一处 SQLModel `select()`；01 的通道是 FTS 召回 + BM25 打分，本票是 LIKE 兜底。修完 01 后 NULL 属主 id **确实会**流进 `candidate_ids`，但 LIKE 是**平行的另一条 SQL**，它自己不过滤、自己的结果直接并入。两处都要修，缺一不可。
  - **修法复用同一口径**：`or_(MemoryItem.user_id == principal.user_id, MemoryItem.user_id.is_(None))`，与 `fts.py` / 向量通道 / 其余读侧 scope 一致。写侧（`ensure_can_delete`）形状不同，不能照抄。
  - **测试增量 3 例**（`tests/test_keyword_fallback_owner_recall.py`，不 mock：真跑 `_keyword_fallback`，显式传 `session=` 内存库避免走宿主真实库）。**Red 实证**：1 failed / 2 passed——红的正是 NULL 老行被滤掉那条。
  - **变异验证 3/3 被杀**（`.scratch/fts-null-owner/mutation_check_03.py`）：V1 退回严格等值、V2 去掉 NULL 半边、V3 整段归属过滤消失（验证隔离没放松——别人的行不泄漏）。还原校验逐字节一致。
  - **给后来者的两个坑**：① `_keyword_fallback` 没有 `use_rerank` 形参（降级路径不跑精排）。② 必须显式传 `params=RetrievalParams()`：函数体 `hybrid.py:939` 用的是 `params.temporal_asof_strict` 而**不是**第 824 行算出的 `p`，传 `None` 会 `AttributeError`。这是产品代码里 `p = params or RetrievalParams()` 之后的漏网点，任何直调该函数的测试/探针都会撞上——**非本票范围，但记在这里省下一次排错**。
  - **全量 pytest 1935 passed / 0 failed**；lint 全绿。中途红过一次 `test_release_check.py::test_lint_gate_passes_on_real_repo`（新测试文件 import 顺序）——**回归哨兵当场干活**，`ruff check --fix` 后复跑全绿。

- **MCP 读侧两个工具不再敞开——`checkpoint_latest` 拿不到 B 的工作现场，`scratchpad_get` 按 id 拿不到 B 的札记（2026-09-28，票据 `.scratch/mcp-identity-gaps/issues/05-mcp-read-side-none-leaks.md`）**：
  - **先说影响**：宿主不透传 `user_id` 时（MCP 没有 HTTP 鉴权层，这是常见形态），两个工具的收窄被 `principal is None` **整条跳过**。`checkpoint_latest` 更糟——它**根本不看 `session_id` 参数**，永远返回全库 `created_at` 最新的那个 session 的完整五段底本（在做/下一步/工作区/决策/待办）。A 只要调一次，就拿到"当前最新的工作现场"，而那个现场很可能是 B 的（B 刚写完底本）。`scratchpad_get` 是按 id 定点取：A 传 B 的 `session_id`，拿到 B 的札记正文——而札记**直接进 LLM 提示**（`format_scratchpad_context`）。实测（`.scratch/mcp-identity-gaps/probe_02_read_scope.py`，真 handler + 内存 SQLite 真建表，库里 2 行 NULL + 2 行 default + 2 行 user-B）。
  - **同批探针的对照说明这是口径分叉，不是普遍现象**：`candidates_pending` 在无身份时收敛到 `"default"`（B 不可见，正常），`mem_recent` 是全表（已知，归 02 号契约票），**只有这两个是"连 id 都按定点取却不过滤"**——它们与 `mem_recent` 的差别是**确定性**：`mem_recent` 是列表，这两个按 id 精确定位，A 知道 id 就能稳定拿到那一条。
  - **根因是同一个形状**：两个函数都把 `principal=None` 当成"内部 worker，不过滤"，而 MCP 入口的 None 语义是"宿主没透传身份"——**不是**"内部 worker"。`get_latest_checkpoint` 的 `_checkpoint_scope(None)` 直接 `return None`，一条 `where` 都不加（它的 docstring 写"scope 加在挑 newest 那一跳上，不是只加在取行那一跳"——**作者预判了"先挑 newest 再取行"这个绕路，没预判 None 会整条跳过**）。`get_scratchpad` 的 `if principal is not None and not is_admin` 把归属判断整块跳过，而上一行 `_owner_of(None)` 已经算出 viewer=`"default"`——**算好了却不用**。
  - **真实库清点决定了修法**：82 行底本 **100% 是 NULL 属主**（都在 04 号票之前写的），全库只有 `default` 一个真实用户。所以"读收敛到 `default` + `OR IS NULL`"对当前部署**零影响**（82 行老底本照常可见），同时挡住未来的 B。**04 号票是这个修法能成立的前提**：无身份**写入**现在也落 `"default"`，读写正好配对，不会出现"自己写了读不到"。
  - **契约变更，改了一条既有测试**：`test_checkpoint_ownership.py::TestCheckpointInternalCall::test_internal_call_unfiltered` 断言 `principal=None` 仍能读 `user-B` 的底本——**那正是本票要关的洞**。grep 实证该场景不存在：`get_checkpoint` 全仓唯一调用方是 `routes_checkpoint.py:43`（HTTP，`get_current_user` 永不返回 None），**没有任何 worker / CLI / 脚本调用者**。改成 3 条新断言（读不到 B 的、仍读 NULL 老行、仍读 default 属主），并在类 docstring 写明这是契约变更 + 实证过程。**同票 04 的教训第二次应验：docstring 说的场景要 grep 过才算数。**
  - **`inject_checkpoint_context` 的自动注入路径没断**（内部 worker 传 `principal=None`）：收紧后它收敛到 `"default"`，而 04 号票让无身份写也落 `"default"`——`test_checkpoint_service.py:119` 那条"无身份写 + 无身份读"的组合继续绿，全量 1905 passed 实证。
  - **`mem_recent` 的 unfiltered 语义不在本票修**：那是 02 号契约票的核心决定（收紧会把现有客户端全打断）。本票只修两个按 id 定点取的工具。
  - **测试增量 9 例**（`tests/test_mcp_read_side_none_leaks.py`，全部不 mock：真 handler + 真 service + 内存 SQLite 真建表）。**Red 实证**：3 failed / 6 passed——红的正是三个泄漏路径，绿的六个是护栏（admin 全量、透传用户见自己的、NULL 老行可见、注入链不断）。
  - **变异验证 5 KILLED / 0 MISSED**（`.scratch/mcp-identity-gaps/mutation_check_05.py`，**两个目标文件**——两个函数的缺口各占一半，subprocess 隔离 + atexit 还原 + 还原后逐字节复核）：V1 None 不过滤（退回修前）、V2 丢 `OR IS NULL`（老行不可见）、V3 admin 也收敛（把关起来）、V4 恢复 `principal is not None` 前置、V5 去掉 admin 分支。
  - **探针自己的 bug 冒充"基线红"**：`mutation_check_05.py` 第一轮报"基线: FAIL"，直接跑同一批测试却全绿。真因是 `TESTS` 写成 `"a.py b.py"` 一个字符串，subprocess 不当拆分 → pytest 返回 `rc=4`（**用法错误**，不是测试失败），被我当成 FAIL。改成列表 + 显式判 `rc == 4` 后正常。**教训：判定不可信时先怀疑探针。**
  - **全量 pytest 1905 passed / 0 failed**（04 后基线 1894）。中途红过一次——`test_release_check.py::test_lint_gate_passes_on_real_repo` 抓到新测试文件没 `ruff format`，**回归哨兵当场干活**，format 后复跑全绿。ruff check + format 均过；gitleaks 本次改动文件 0 命中。

- **MCP `checkpoint_write` 不再落无主底本——宿主不透传身份时归 `default`，工作现场不再对所有人敞开（2026-09-28，票据 `.scratch/mcp-identity-gaps/issues/04-checkpoint-write-null-owner.md`）**：
  - **先说影响**：这是 02 号票第一步清点（60 个 handler 机械枚举）顺带挖出来的洞，形状和前三波都不同——**principal 取了、也传了，但下游在 `principal=None` 时把 owner 落成 NULL**。而底本的读侧口径是 `user_id == viewer OR user_id IS NULL`，**NULL 行的可见性是"人人可读"**。于是宿主不透传 `user_id` 调 MCP `checkpoint_write`，落一行无主的五段快照（在做/下一步/工作区/决策/待办），之后**任何**用户调 `checkpoint_latest` 都能读到它——那是 Agent 的当前工作现场，`readside-gaps/09` 专门说过"把当前工作现场整个吐出"有多严重。
  - **同批探针的对照组说明这是孤例**：`raw_add` / `add_dialogue` / `scratchpad_write` 在不透传身份时全部落 `"default"`，**只有 `checkpoint_write` 落 NULL**（实测 `.scratch/mcp-identity-gaps/probe_02_write_owner.py`，真 handler + 内存 SQLite 真建表）。
  - **docstring 里的"内部调用"根本不存在**：`write_session_checkpoint` 原写"`principal=None` 的内部调用留 NULL，与改动前逐字一致"——grep 全仓只有 `routes_checkpoint.py:53`（HTTP）与 `mcp.py:878`（MCP）两个调用方，**没有任何 worker / CLI / 脚本调用者**。那条设计对应的场景不存在，只剩洞。HTTP 侧也一直是好的（`get_current_user` 永不返回 None，DEV MODE 回落 `Principal(user_id="default")`）——**这个洞只有 MCP 入口能触发，第二个入口面的典型代价**。
  - **修法一行**：owner 从 `getattr(principal, "user_id", None)` 改成 `acl.viewer_of(principal)`（收敛：`None`/空 user_id → `"default"`）。tenant / agent 保持 `getattr(..., None)`——它们没有"人人可读"的读侧口径，改了只会扩大回归面。admin（真形态 `user_id=None`）落 `"default"`，读侧 `== viewer OR IS NULL` 照样放行，**不会把 admin 关在门外**。
  - **这不违反 02 号票"读操作不收紧"的价值观**：改的是**写入时落哪个属主**，读侧一个条件都没动。`test_null_owner_legacy_rows_still_visible` 专门钉住这点——NULL 属主老底本对任何用户仍可见，单人部署不丢历史。
  - **未改 `crystal_service.py:84`**：同形状但 NULL 是**刻意的**（docstring 明写"后台巡检留 NULL"，且确有巡检调用者）。同一种代码形状在两处一个是对的、一个是错的，差别只在"设计说的场景是否真实存在"——**这正是要逐个 grep 调用者而不能按形状批量改的原因**。
  - **测试增量 6 例**（`tests/test_checkpoint_write_owner.py`，全部不 mock：真 handler + 真 service + 内存 SQLite 真建表）。**Red 实证**：3 failed / 3 passed——红的正是三个 NULL 落库路径，绿的三个是回归护栏（不依赖修复）。
  - **变异验证 3 KILLED / 0 MISSED**（`.scratch/mcp-identity-gaps/mutation_check_04.py`，subprocess 隔离 + atexit 还原 + 还原后逐字节复核）：V1 退回 `getattr`（修前形状）、V2 硬写 `"default"`（不认透传的 user_id）、V3 `owner = None`（落到列默认 NULL）。
  - **全量 pytest 1894 passed / 0 failed**（03 后基线 1888）。ruff check + format 均过；`scripts/release_check.py` 的 CI lint 门禁与版本一致性均 PASS；gitleaks 本次改动文件 0 命中。

- **VAULT 档案页不再空白——`build_memories_page` 补上 `OR IS NULL`，DEV MODE 从只见 3/650 条恢复为 632/650（2026-09-28，票据 `.scratch/mcp-identity-gaps/issues/03-vault-page-missing-or-is-null.md`）**：
  - **先说影响**：这是**功能不可用**级别的坑，不是"收紧了点"的体验问题。真实库 650 行 `memoryitem` 里 **629 行 `user_id` 是 NULL**（v022 之前写入还没有属主概念），而 `build_memories_page` 的归属条件是裸的 `user_id == principal.user_id`，**没有 `OR IS NULL`**。于是 DEV MODE（或任何显式属主的用户）打开 VAULT 档案页 / 终端图谱 / 让 MCP `mem_recent` 取最近记忆，**只能看到自己那几条，629 行历史记忆全部不可见**。实测：`principal=None` 650/650，DEV MODE **3/650**，user-A 6/650。**单用户部署下档案页基本是空的。**
  - **同文件 5 个收窄点，4 个对 1 个错**：`core memory 读/写`（:503/:532）、`verbatim 去重`（:639/:644）的口径全是 `user_id == viewer OR user_id IS NULL`，**只有 `build_memories_page`（:737）漏了后半截**。根因是它的归属块按字段逐条 `if` 追加（tenant/user/session/agent/allowed_lanes 各一条），写的时候只想着"有这个字段就加条件"，**没走 `viewer_of` 那套收敛口径**——所以既没有 `OR IS NULL`，也没有 admin 分支。
  - **修法最小**：只把 user_id 那一条换成同文件口径 `(MemoryItem.user_id == viewer_of(principal)) | (MemoryItem.user_id.is_(None))`；tenant / session / agent / allowed_lanes 四条**原样不动**（它们没有 NULL 老行问题，动了只会扩大回归面）。用 `acl.viewer_of` 而非直接取 `principal.user_id`：前者对空 user_id 回落到 `"default"`，与 DEV MODE 同值，**不新造默认属主**。三个入口（`GET /memory/list`、`GET /terminal/graph`、MCP `mem_recent`）共用这一个函数，**修一处三处都好**。
  - **`OR IS NULL` 的语义必须写清楚，否则下一个人会"修反"**：它是"NULL 属主老行人人可读"，**不是"不隔离"**——B 的新行对 A 仍然不可见。测试 `test_explicit_owner_sees_null_rows_but_not_others` 同时钉住这两个方向：A 该看到 629 NULL + 自己 = 630，且 `"m-b" not in ids`。
  - **实测推翻了我自己的一个误判，特此留档**：第一轮探针传了个 `user_id='a'` 的 admin 进去，得 0 条，我据此判"admin 分支缺失、要补"。复核 `auth.py:168` 后确认**真 admin 的 `user_id` 是 `None`**（`make_principal("api_key", list(DEFAULT_LANES), role="admin")`），于是 `if getattr(principal, "user_id", None)` 为假 → 不加任何条件 → 看到全部 650。**admin 路径没有问题，不修**。新增的 `test_admin_unchanged` 专门锁住这个真形态，免得下一个人照着我第一轮的错误结论去给 admin 加一个分支——那会真的把 admin 关起来。
  - **测试增量 4 例**（`tests/test_vault_null_owner_rows.py`，全部不 mock：真 `build_memories_page` + 内存 SQLite 真建表，要验证的正是那个 `where` 条件的形状）。其中两条是回归护栏：`test_none_principal_unchanged`（`principal=None` 内部 worker / 宿主不透传时仍全表——02 号票"读操作不收紧"的边界）与 `test_admin_unchanged`。**Red 实证**：修前 `test_dev_mode_sees_null_owner_rows` 得 3，修后 632。
  - **变异验证 3 KILLED / 0 MISSED**（`.scratch/mcp-identity-gaps/mutation_check_03.py`，subprocess 隔离 + atexit 还原 + **还原后逐字节复核**）：V1 删掉 `OR IS NULL`（退回修前）、V2 整个归属条件不要（放开隔离，B 的记忆漏进来）、V3 只要 `IS NULL`（老行人人可读、自己的反而不读）——三个方向全杀。
  - **全量 pytest 1888 passed / 0 failed**（01c 后基线 1884）。ruff check + format 均过；`scripts/release_check.py` 的 CI lint 门禁与版本一致性均 PASS，仅"工作区不干净"（本次提交自身）与"tag v0.22.1 已存在"（已发布）两项 FAIL；gitleaks 本次改动文件 0 命中。
  - **这条是 02 号契约票的第一步（机械清点 55/60 个 handler 的 `None` 语义）顺带挖出来的**：清点本身没写完，但已足以暴露"`None` 的语义每个函数都不一样"——`build_memories_page` 的 `None` 是全表、`candidate_service._owner_scope(None)` 收敛到 `"default"`、`build_overview` 的 `None` 是全表（内部 worker 不能断）。**这正是 02 号票要逐工具定表的原因。**

- **MCP 层第三批：`triage_auto_pilot` 与 `backfill` 补上身份——A 不再能裁决别人的待审候选、篡改别人的检索回执（2026-09-28，票据 `.scratch/mcp-identity-gaps/issues/01c-late-found-handlers.md`）**：
  - **先说影响**：`triage_auto_pilot`（自动巡检）内部两步都踩在已修好的收窄逻辑上，但它自己四个参数一个身份都不收，**没往下传**——A 跑一次就能自动驳回/批准别人的待审候选。`backfill` 更阴：A 拿 B 的 `event_id` 调一次，就把 B 的回执从 `pending` 改成 `acked` 并覆盖成 A 给的值。**后者不是读泄漏，是脏写**——回执链是 ADR-0049 弱标注的地基，被污染后 dry-run 的 `weak_hit_rate` 算的就是假数。
  - **两处都是"下游已有 `principal` 形参、只差一根线"**（同 01a 性质，不是 01b 的改内核）：`run_ai_triage` / `apply_ai_triage_batch` 的收窄逻辑票 readside-gaps/02 已写好并杀过变异，这里不发明第二份判据。`backfill` 是现成的 `RetrievalEvent.user_id` 列比对（不是 21 张无归属列表那一类）。**HTTP 侧同步接线**（`/retrieval/backfill` 补 `ctx=Depends(get_current_user)`），两条入口面走同一份收窄。
  - **`consolidation_report` 按调研定性降为 P2，只写实不收紧**：逐键读完 12 个键，**是纯计数**（候选数/合并数/衰减数/耗时），一条记忆正文、一个 id、一个 user_id 都没有。改成在 docstring 如实标注「这是全库计数，不按调用方收窄」，两个入口（HTTP + MCP）口径一致。**要不要真收紧归 02 号契约票**——收了等于单人部署看不到全局治理状况，是契约级决定。
  - **C2 变异体的曲折（本票最值得记的一条）**：`apply_ai_triage_batch` 去掉 `principal` 这个变异体第一轮报 MISSED。两轮探针才定性：走 MCP 入口时它**不可观测**——`_owner_scope` 只判 `user_id`，`ensure_can_delete` 判 `user_id + tenant + lane`，而 `actions_to_apply` 里只会有 `user_id == viewer` 的候选，那批 `ensure_can_delete` 一律放行，**两处判据重合，砍掉一处确实不影响结果**。但 service 层传一个 lane 绑定的 Principal 后基线 `failed=1`、变异体 `rejected=1`——**候选被真的驳回了，C2 是真漏洞**。补 `test_auto_pilot_adjudicate_respects_lane_binding`（service 层，因为 MCP 的 `_principal_from_params` 把 `allowed_lanes` 硬编码 `None`，构造不出 lane 绑定身份）后转 KILLED。**教训：判据的差集正是变异体能活下来的空间**——"下游已有收窄，这里补一根线"的修复要逐个比对新旧判据，重合的才算冗余，有差集的必须分别测试。
  - **测试增量 7 例**（`tests/test_mcp_principal_wiring.py`，`TestTriageAutoPilotScoped` + `TestBackfillScoped`，全部不 mock）。其中 `test_backfill_allows_null_owner_event` 专杀 C4：NULL 属主老行**必须**可回填——真实库 918 条检索事件只有 68 条带 `session_id`，挡掉 NULL 等于单人部署的回执链整体失效，**那是修废不是收窄**。
  - **变异验证 4 KILLED / 0 MISSED**（`mutation_check_01c.py`）；**护栏反方向变异 3 KILLED / 0 MISSED**（`mutation_guard_reverse.py`：新增漏接线 handler、EXEMPT 僵尸条目、handler 改名漏枚举）。结构性护栏删掉已修好的两个豁免条目时**当场红了一次——正是它该干的活**。
  - **全量 pytest 1884 passed / 0 failed**（01b 后基线 1877）。ruff check + format 均过；gitleaks 72 条命中全在 `.venv/` 与既有 `.scratch/` 产物，本次改动 0 条。`.gitignore` 补 `.env.bak*`（CI 测试临时造的 .env 备份此前不在忽略名单里）。
  - **一个操作教训**（已入记忆 `mutation-probe-must-verify-restore`）：探针脚本连跑三轮后产品代码实际停留在**变异态**，而它每轮都打印"已还原"——是下一个探针的开头 assert 抓到的。**还原必须复核**，两个探针的 finally 现在都改成"还原 + 逐字节比对"。

- **MCP 层第二批 11 个工具补上归属收窄——A 不再能起复 B 的记忆、读 B 的卸载全文、拿 B 的异步任务结果（2026-09-28，票据 `.scratch/mcp-identity-gaps/issues/01b-service-needs-principal.md`）**：
  - **先说影响**：01a 那波是"下游 service 已有 `principal`、只差一根线"的纯接线；这一波的 11 个工具**下游 service 连 `principal` 形参都没有**，所以要在 service 层补收窄逻辑——而那层被 HTTP 路由与内部 worker 共用，**改签名有真实回归风险**（`revive_consolidated` / `run_autodream_once` 尤甚）。修完后 A 通过 MCP 能做的越权操作：起复 B 的整簇记忆（破坏性、`archived` 不可撤销）、按 `memory_id` 读 B 的卸载全文、拿 B 的 `task_id` 读到 B 的对话提取结果、下钻纯 B 的场景读到全成员正文、读 B 的 wiki 页、在 A 的用量报表里看到 B 的当日统计、把 B 的记忆聚进 A 的蒸馏簇。
  - **service 层 7 个文件是承重墙**：`revive_consolidated` 的归属校验**下沉到 service**（不在 MCP handler 里另写一份）——HTTP 侧顺带受益，且全仓只有一份判据（复用 `acl.ensure_can_delete`，它只做「资源是否属于这个主体」的判定，实现里没有任何删除动作）。`offload_read` / `wiki_read` / 场景这一类**资源本身没有归属列**（文件系统与渲染产物），判据按 `memory_id` / `slug` **反查载体记忆的属主**——同 `resolve_probe_response` 对 `ConflictEvent` 的过渡推导。`create_skill` 用**内联属主四元组**而非 `_owner_of`：后者对 `None` 返回 `(None, "default", None)`，会把"没身份"写成"属 default"。
  - **`OR IS NULL` 是这条口径的命门**：可见判据一律 `user_id == viewer OR user_id IS NULL`——NULL 属主老行**必须可见**。真实库 650 行里 629 行 NULL，判不可见等于单人部署下报表归零、场景全空、蒸馏空转，**那是修废不是收窄**。`ops/usage.py` 与 `evolution/autodream.py` 最初只写了 `== viewer`，与自己的 docstring 相悖，被 `test_usage_counts_today` 抓出来——doc/code 分叉比没有 doc 更危险。
  - **HTTP 侧同步接线**（4 条路由 + 2 个 service 调用点）：让两条入口面走同一份收窄，不保留"HTTP 不过滤"的第二条路径。`handle_add_dialogue` / `handle_dialogue_add_async` **故意不调** `_principal_from_params`——它们的身份来源就是 `params.user_id` 本身，再取一次是两套口径。
  - **测试增量 31 例**（`tests/test_mcp_principal_wiring.py`，9 个测试类，全部不 mock：真 `handle()` 分派 + 内存 SQLite 真建表 + `patch db.get_session`，替身只覆盖 embedding/向量存储）。**Red 先行实证**：`git stash push lantai/` 后 15 failed / 15 passed——每条收窄用例都在未修代码上红。
  - **变异验证 18 KILLED / 1 MISSED**（`mutation_check_01b.py`，19 个变异体跨 8 个文件，subprocess 隔离 + timeout + atexit 还原）。第一轮 17/2，逐条查那两条：M4 经实证是**等价变异**（MCP 只有一个 Principal 构造点保证 `user_id` 非空，`viewer_of(p) ≡ p.user_id` 在本入口不可区分）——**写明理由留在清单里，没写自欺欺人的测试**；M6 是**真测试缺口**：孤儿卸载文件的断言写成 `assert "error" in resp`，而 MCP 的 `handle()` 把 `FileNotFoundError`（按设计拒绝）和 `AttributeError`（变异体撞崩）**都转成 -32603**——断言分不清"按设计拒绝"与"崩了"。改成断言错误类型含 `"FileNotFoundError"` 后杀掉。**教训：断言"有错误"不够，必须断言是哪一个错误。**
  - **全量 pytest 1877 passed / 0 failed**（基线 1856；第一轮全量跑时结构性护栏红了一次——正是它该干的活：我加了 11 个接线却没更新手写的 expected 集合，见下条）。
  - **探针前后对比**：取身份的调用点 `40 → 52`。场景 3（宿主不透传 `user_id`）修前修后都是全表——**这条不变正是本波的边界**，收紧它是 02 号契约票的事。
  - **踩坑三则**：① `get_current_user` 在 `lantai/core/auth.py` **不在** `core/acl.py`（`Principal` 才在 acl.py）。② `SelectOfScalar` **没有 `.first()`**——必须 `s.exec(select(...).where(...)).first()`，写在 `select()` 后面运行期才炸，且 `ruff format` 会把正确写法重排成错的那版。③ service 签名变了就要同步所有 `assert_called_once_with` 精确调用断言（`tests/test_mcp.py` 一处红在这）。
  - **结构性护栏从"数人给的集合"升级为"机械枚举全部 handler + 显式豁免清单"**：原护栏断言 48 个手写进 `expected` 集合的 handler 都取了身份——**集合本身是人从普查表誊的，誊漏了就查不出来**。01a/01b 两波合起来誊漏了 4 个（`triage_auto_pilot` / `consolidation_report` / `backfill` / `mem_health`），护栏全程绿。新护栏正则枚举 `mcp.py` 里所有 `def handle_`，逐个要求「取身份」或「在 `EXEMPT` 字典里且写明理由」，并反向检查豁免清单无僵尸条目。**已用两个方向的变异实证它能红**：① 把 `mem_sync` 的 `principal=` 删掉 → 红；② 新增一个忘了接线的 handler → 红（旧护栏对②完全无感，这正是 01a/01b 的失效形状）。
  - **顺带发现 7 个 handler 仍没接线**（机械核对 `inspect.getsource`，不靠人眼誊清单）：2 个真缺口 + 5 个不该接，另立 [01c 票](.scratch/mcp-identity-gaps/issues/01c-late-found-handlers.md)。**探针还推翻了我自己的初判**：原以为 `triage_auto_pilot` 是"全表扫描、B 的候选被裁决"，实测 `scanned == 1` 且只含 `user_id="default"` 那条——`_owner_scope(None)` 收敛到 `"default"` 而非全表。按实测改票面，没保留错误结论。

- **MCP 层 36 个工具补上身份透传——A 调 `mem_recent` 不再拿到 B 的记忆全文（2026-09-28，票据 `.scratch/mcp-identity-gaps/issues/01a-mechanical-wiring.md`）**：
  - **先说影响**：`lantai/cli/mcp.py` 是**完全独立的第二个入口面，而且没有 HTTP 鉴权层**。`handle()` 只做 JSON-RPC 分派，不构造 `Principal`；身份只能由宿主在 `params` 里自愿透传 `user_id`。60 个工具里只有 5 个取了身份，其余 55 个直接调 service 且不传 `principal`——而 `principal=None` 在仓里的语义被明确定义为「不过滤」。**结论：23 张票在 HTTP 侧修好的每一个洞，在 MCP 侧原样复现。** 复现实证（`.scratch/mcp-identity-gaps/probe_mcp_identity.py`，子进程隔离 + 真 `handle()` 分派 + 内存 SQLite 真建表）：修前 A 调 `mem_recent` 带 `user_id="user-A"`，返回 `user-B` 的记忆全文「收购对家的报价底牌」。
  - **根因在 `build_memories_page`**（memory_service.py:732）：整个归属块挂在 `if principal:` 下，`None` 时**一个条件都不加**。所以 `mem_recent` / `verbatim_search` / `cognitive_context` / `graph_view` / `recall_chain` / `get_digest` / `candidates_pending` 这一整批读侧工具，宿主不传身份就是全表。
  - **本波修法纯接线**：下游 service 函数全部已有 `principal` 形参与收窄逻辑（23 张票的成果），MCP 侧只差一根线——每个 handler 顶部 `principal = _principal_from_params(params)`，然后 `principal=principal` 传进去。范本是既有 5 个 handler（`handle_search` / `handle_feedback` / `handle_rollback` / `handle_reflect_run` / `handle_graph_expand_search`），**没有发明第二份判据**。`add` / `raw_add` / `obsidian_sync` 三个写侧工具另传 `user_id=`（此前落库行的属主恒为 `"default"`，A 下次按归属读时读不到自己刚写的东西）。
  - **顺手修掉一个既有 bug**：`collect_digest_stats` 的 `stats["day"]` 返回裸 `date` 对象，而 MCP 的 `get_digest` 把返回值整体 `json.dumps` 出去 → `TypeError: Object of type date is not JSON serializable` → `-32603`。**宿主只要已生成过当日报告，这个工具就 100% 报错**（`run_digest_once` 那条分支恰好先调了 `.isoformat()` 才没暴露）。改成在源头序列化成 ISO 字符串，同步改三处消费方。HTTP 侧不受影响（FastAPI 的 `jsonable_encoder` 认 date），**只有 MCP 侧炸**——这就是"第二个入口面"的典型代价：同一个函数在两侧的序列化契约不一样。
  - **测试增量 10 例**（`tests/test_mcp_principal_wiring.py`，全部不 mock：真 `handle()` 分派 + 内存 SQLite 真建表 + `patch db_module.get_session`，替身只覆盖 embedding/向量存储）。其中一条是**结构性护栏**：直接读 `inspect.getsource` 断言 36 个 handler 的源码里都出现 `_principal_from_params(params)`——票 24 的教训是「签名有 principal、函数体不用」这个形状审查看不出来；另一条护栏锁住 `_principal_from_params` 不许开始看环境变量/进程名（那等于猜身份，比 NULL 更难排查）。
  - **回归边界全部保住**（宿主不透传 `user_id` 时行为与改动前逐字一致）：这是**故意的**，收紧它是 `.scratch/mcp-identity-gaps/issues/02-mcp-identity-contract.md` 那一票的范围——一次性收紧会把现有 MCP 客户端（普遍不传 `user_id`）全打断。既有 `tests/test_mcp.py` 46 例继续全绿，其中 4 处 `assert_called_once_with` 同步补上 `principal=None`。
  - **对普查结论的一处修正**：普查称「55 个 MCP 工具全部全表」，实测**不准确**——`candidates_pending` 在 `principal=None` 下收敛到 `user_id == "default"`（`_owner_scope(None)` → `_viewer_of(None)`），不是全表。「None = 全表」这个说法必须按函数逐个核。
  - **变异验证 12/12 全杀**（`.scratch/mcp-identity-gaps/mutation_check_01a.py`，subprocess 隔离 + timeout + atexit 还原）。第一轮 10 KILLED / 2 MISSED，逐条查那两条是**锚点失配不是真存活**——`ruff format` 把脚本里写的多行原串压成了一行，`old not in original` 判成 0 处。改成单行锚点后复跑 12 KILLED / 0 MISSED。**教训同票 24**：变异脚本报 MISSED 先看是不是锚点问题，别急着补测试。
  - **探针前后对比**：取身份的调用点 `4 → 40`；场景 1「B 的记忆进来了？」`True → False`（返回 0 条）；场景 3（不透传身份）修前修后都是 1 条——**这条不变正是本票的边界**。全量 pytest **1856 passed / 0 failed**（基线 1846）。
  - **未修**：13 个下游 service 还没有 `principal` 形参的工具（`.scratch/mcp-identity-gaps/issues/01b-service-needs-principal.md`，要动 service 层，回归风险不同）；MCP 身份契约本身（02 号票）；21 张无归属列的表（要迁移）。

- **终端读路由补归属——`GET /terminal/memory/{id}` 不再把别人的完整记忆吐给任意持 key 者（2026-09-28，票据 `.scratch/readside-gaps/issues/24-terminal-get-memory-no-ownership.md`）**：
  - **先说影响**：`get_single_memory`（routes_terminal.py:227）签名里**有** `principal=Depends(get_current_user)`，函数体却一次都没引用它。同文件另外 9 个 `/terminal/memory/*` 路由全过 `_check_ownership`（:367，P0 票04 给笔削四操作建的），**只有这条读路由漏了**。后果：A 拿 B 的 memory_id 一次请求拿到**完整 41 字段**——`content` 全文、`structure`/`provenance`/`tags` JSON、`confidence`/`importance`/`decay`、`lifecycle_status`、`superseded_by`。复现实证（`.scratch/readside-gaps/probe_24_terminal_get.py`，子进程隔离 + 真 `TestClient` + 真 `get_db_conn`）：修前场景 1 回 `200` + 41 字段，场景 6（资源在 A 的 `allowed_lanes` 之外的 lane）同样回 `200`。
  - **这条比"没有 Depends"更危险**：代码审查看到 `principal=Depends(get_current_user)` 就会打勾。本轮路线普查（`.scratch/route-census-2026-09/census.md`，193 个入口：133 条 HTTP 路径 + 60 个 MCP 工具）里它是**唯一一条"签名有、函数体不用"的形状**。同一个模式第三次出现——票 22 修 `/edges/{id}` 时也是"同一个文件里修了一条、漏了旁边那条"。
  - **修法一行**：`return m.model_dump()` 之前插 `_check_ownership(session, memory_id, principal)`。口径**不发明第二份**——`ensure_can_delete` 名字带 delete，但它只做「资源是否属于这个主体」的判定（`core/acl.py:117-126` 的 docstring 明说"删除等破坏性操作的资源归属校验"，实现里没有任何删除动作）。为此在 `_check_ownership` 的 docstring 补了一段"读路由同样适用"，免得下一个人又因为名字不敢用、另写一套判据（票 05 `_monitor_full_view` 口径漂移就是这么来的）。
  - **回归边界全部保住**（修完必须成立，否则把人修废）：admin 取任意记忆仍 `200`；NULL 属主老行仍 `200`（单人部署下这是大量真实历史记忆，判不可见等于读不了历史）；不存在的 id 仍 `404` **而不是 403**——404 语义是"不存在"、403 是"存在但不归你"，混用会让 A 拿状态码差异当 id 枚举预言机（票 19 记录过的攻击材料）；A 取自己的仍 `200` 且 41 字段响应形状不变（本票只加归属校验，不改响应契约）。
  - **测试增量 10 例**（`tests/test_terminal_get_ownership.py`，全部不 mock：真实内存 SQLite 建表 + monkeypatch `db_module.engine` + 路由层真 `TestClient`）。其中一条是**结构性护栏**：直接读 `inspect.getsource(get_single_memory)` 断言它调了 `_check_ownership`——本票的 bug 形状正是"签名有 principal、函数体不用"，HTTP 断言拦不住"绕路拿到数据"的实现。
  - **变异验证 9/9 全杀**（`.scratch/readside-gaps/mutation_check_24.py`，subprocess 隔离 + timeout + atexit 还原，**两个目标文件**——`routes_terminal.py` 与 `core/acl.py`，归属口径的真实承重墙在后者）。第一轮 7 KILLED / 3 MISSED，逐条诊断：M4/M5 的用例里资源 `user_id` 也跟 A 不同，**用户判据先拦住**，lane/tenant 分支永远不是决定性的一步——补了"资源属主是 A 自己、lane 是唯一拦截者"与"user_id 留空、tenant 是唯一拦截者"两条对偶，外加一条正面对照（自己 lane 内该放行，否则"lane 一律拒"的等价实现也能绿）；M8 经实证是**等价变异**（两种写法只在 `resource_user_id == ""` 时不同，而空串 user_id 在 MemoryItem 上产生不出来——列默认 NULL，`OperationLog` 的兜底哨兵是字面量 `"anonymous"`，空串只出现在审计事件的 `actor` 字段），**从清单移除并写明理由**，没去写一条自欺欺人的测试。
  - **探针的"失败"也可能是探针自己的 bug**：第一版场景 2-5 全 404，看着像"修好了"。真因是 `with TestClient(app)` 进出一次会跑 lifespan 的 startup/shutdown，**把 `db_module.engine` 重置回宿主库**。改成每次新建客户端、只打一次请求后才拿到真实的七场景对比。**教训**：先确认夹具真的连上了被测库，再解读结果。
  - 全量 pytest **1846 passed / 0 failed**。

- **认知写侧不再丢身份——A 的观测不再人人可见，A 的反思不再扫全表（2026-09-28，票据 `.scratch/cognitive-write-gaps/issues/01-cognitive-write-no-identity.md`）**：
  - **先说影响**：`routes_cognitive_router` 在 `CORE_ROUTERS` 里，`dependencies=AUTH` 认证了某人——**然后把身份丢掉了**。四个 handler 里两个读的已修（票 03/14），两个写/触发的一个都没取 `ctx`。① `POST /cognitive/observe` 落的 `MemoryItem` 与 `Evidence` 两行全是 `user_id=NULL`，而 NULL 在本仓的口径是"未记录归属的老行，人人可见"——**A 写的观测对所有用户可见**，且入参是自由文本 `content`，写什么完全不受控。② `POST /cognitive/reflect` 的 `run_reflection` 四个查询一个 scope 都没有：统计是全表数、A 与 B 的观测被**一起聚类**、晋升出的 Belief/Rule 候选经 `db.add` 落库又是不带 `user_id` 的 ownerless 行——**从跨用户聚类里长出来的 ownerless 行，比 observe 那条更脏**。复现实证（`.scratch/cognitive-write-gaps/probe_cognitive_write.py`，子进程隔离 + 真实 `TestClient` + 真 `ReflectionEngine` + 内存 SQLite 真建表）：修前 A 的 observe 落 `user_id=None`，A 触发的反思 `principles_under_review == 1` / `rules_weakened == 1`（全是 B 的行）。
  - **修法三处，口径全部复用既有收敛真源**（不发明第二份 scope）：① observe 两处构造补四元组，取值带 `"default"` 兜底（同 `obsidian_service.py:158`；`session_id` 不加——认知观测不是会话产物）。② `ReflectionEngine.__init__(self, db, *, principal=None)`，observation / belief / rule 三个查询加 `_reflection_scope`（`user_id == viewer OR IS NULL`，NULL 老行可见——单人部署下判不可见等于反思空转）；`principal=None`（内部 worker/脚本/MCP）与 admin 全量。③ 晋升的 Belief/Rule 候选过 `_stamp_owner`。**DEV MODE 特例同票 05**：无凭证回落到 `Principal(user_id="default", role="user")`——**不是 None**，于是照常收窄（"无凭证的本机请求"与"内部 worker"是两回事）。
  - **`FailureRecord` 无归属列，如实标注而不静默丢**：`ReflectionReport` 加 `failures_scoped`，`run_reflection` 内置为 `False`，worker 的 `cognitive_reflection` 同步透传。谎报 `true` 会让读报告的人把跨用户的失败数当成自己的——**比不报更糟**。不给它加归属列：那是迁移，另开票。
  - **测试增量 11 例**（`tests/test_cognitive_write_ownership.py`，全部不 mock：真实内存 SQLite 建表 + 真 `ReflectionEngine` + 路由层真 `TestClient`）。夹具踩到 `dependency_overrides` 的**对象同一性**陷阱：`routes_cognitive.py` 写 `from lantai.storage.db import get_session`，在本模块命名空间留了原始函数，`patch("lantai.api.routes_cognitive.get_session")` 动不了路由已绑定的依赖——必须 `route_dep = routes_cognitive_mod.get_session; app.dependency_overrides[route_dep] = ...`（票 03 同款教训，已写进夹具 docstring）。
  - **变异验证 12/12 全杀**（`.scratch/cognitive-write-gaps/mutation_check_01.py`，subprocess 隔离 + timeout + atexit 还原）。第一轮 **8 条 MISSED**，逐条定性全部是「判据恒假」而非等价变异：夹具没种 Belief/Rule 行（衰减分支从未求值）、断言写成 `report is not None` / `>= 1` 这类恒真式。补夹具（A/B 各一条低置信 Belief 与 Rule）并把断言改成具体数字（A 视角 `principles_under_review == 1`、admin/None 视角 `== 2`）后全杀。
  - **写测试时实证判据会失败，别推算**：`test_reflect_promoted_rows_carry_identity` 想让 pattern 真晋升出 Belief，连改三版才成。根因是 `pat.source_ids` 取自**观测行自己的 `source_ids` 列**（`evolution.py:102`），而 `propose_beliefs` 只在它非空时才查 Evidence（`evolution.py:144`）——把 Evidence 的 id 放进去命中 0 行，`independent_support` 恒 0，得分上限 0.595 < 阈值 0.70，候选永远是 0。用调试脚本实证（`dbg_score.py`）确认 `source_ids` 里该放**记忆 id**：接好后 `eq=0.9 / indep=1.0 / rec=0.667 → score=0.755` 越过阈值。**教训同票 05**：断言恒真的测试杀不掉变异。
  - **探针前后对比**（同一脚本跑 `git checkout` 还原的旧码与修好的版本，两份输出都留档）：observe 落的 `MemoryItem.user_id` `None → 'user-A'`、`Evidence.user_id` `None → 'user-A'`；A 触发反思的 `principles_under_review` `1 → 0`、`rules_weakened` `1 → 0`；`failures_scoped` 字段从不存在到如实回 `False`。全量 pytest **1835 passed / 0 failed**。

- **监控面板不再把宿主机指纹与全库统计吐给任意持 key 者（2026-09-28，票据 `.scratch/readside-gaps/issues/05-monitor-leaks-host-internals.md`）**：
  - **先说影响**：`/monitor/*` 五个 handler 一个身份都不取（模块 docstring 自称"CORE_ROUTERS 注册即鉴权"——**这句注释与事实不符**，`CORE_ROUTERS` 只决定挂不挂载，不注入身份）。于是任意持 key 者一次请求拿到：宿主机绝对路径（**含操作系统用户名** `C:\Users\Asus\...`）、python 完整版本串（`3.13.14`，够直接查 CVE）、`pid`/`uptime_seconds`/`cpu_seconds`/`open_fds`、OS 指纹（Windows 11 / AMD64 / 16 核）、鉴权拓扑（`host`/`port`/`api_keys_*`/`effective_auth`）、LLM 与 reranker 端点地址、**全库**记忆/候选/提案计数、以及**别人**的请求日志。复现实证：`.scratch/readside-gaps/probe_05_monitor_leak.py`（子进程隔离 + 真实 `build_monitor_snapshot` + 内存 SQLite）。
  - **修法分两路不混**。①**宿主机指纹**按身份分档：`process.python` → 只留 `major.minor`（`3.13`），`pid`/`open_fds`/`cpu_seconds`/`uptime_seconds` 撤回，`storage.*.path` → 只回文件名，`security.host`/`port`/`api_keys_*`/`effective_auth`、`dependency.platform` 与两个 `base_url` 撤回。**键保留、值置 `None`**——前端 `monitor.js` 直接模板串 `security.host`/`process.rss_mb`，删键会让 JS 抛异常，脱敏不能把运维修废。②**计数与日志**按归属收窄，口径同票 04 的 `_digest_scope`：`user_id == viewer OR IS NULL`（NULL 老行可见——单人部署 629/650 行是 NULL，判不可见等于报表归零）。
  - **单一判据两个理由**：`_monitor_full_view(principal)` = `principal is None or is_admin`，归属轴与指纹轴共用。`None` 不过滤，是因为内部 worker/CLI/MCP 不带身份，一过滤就空转；指纹轴上 `None` 全量不是 fail-open——**HTTP 路径上 `get_current_user` 永远返回 Principal，`None` 在这条路上产生不出来**。**DEV MODE 特例**：无凭证时回落到 `Principal(user_id="default", role="user")`——**不是 None**，于是照常脱敏（"无凭证的本机请求"与"内部 worker"是两回事，前者是任意持 key 者）。
  - **`operation_logs` 多兜两个哨兵**：遥测写 `row["user_id"] or "anonymous"`（`telemetry.py:103`），真实库 171 行。若只按 `OR IS NULL` 兜老行，这 171 行对任何非 admin 都看不见——那恰是扫描器踩点最可能留下痕迹的一段，**藏它比露它更危险**。
  - **Red 测试顺手揪出的邻近泄漏**：`safe_settings_view()` 原先打码 `DATABASE_URL` 却不打码 `LANTAI_HOME`（同一个目录、同样含操作系统用户名）。现在两个名字走同一条 `_mask_path`。
  - **Prometheus 副作用如实记录**：`gauge()` 对 `None` 直接 return，撤值的指标**整行消失**而非输出空 gauge。这是对的（抓取方会把空值读成 0 或 `nan`）；断言因此分两类：仍在的指标名必须在，已撤的必须不在。
  - **测试增量 29 例**（`tests/test_monitor_ownership.py`，全部不 mock：真实建表的内存 SQLite + `patch db.get_session` + 真 `evaluate_alerts` + 路由层真 `TestClient`）。变异验证 **20/20 全杀**（`.scratch/readside-gaps/mutation_check_05.py`，subprocess 隔离 + timeout + atexit 还原）；第一轮 3 个 MISSED 全是夹具没种 `RetrievalEvent`/`provenance`/`session_id` 导致判据永不成立，补种子后全杀。复现探针前后对比：A 的 `pid` `21732→None`、`python` `3.13.14→3.13`、`storage.database.path` 绝对路径→`remembrance.db`、`platform` 整块→`None`、`memories.total` `3→2`、`logs` `2 条→1 条`；admin 视角原样保留。全量 pytest **1825 passed / 0 failed**。

### Added

- **控制台前端模块化拆分 + 认知终端/案牍审阅/沉淀工作台启用（2026-09-28）**：
  - **拆分**：`ui/app.js` 从 1137 行降到 838 行（净减 299 行），通用能力抽到 `ui/dom.js`（`$`/`node`/`formatDate`/`showToast`/`setAfterUndo`，91 行），业务面板各自独立：`vault.js`（278 行，挂载树/待整理/库房）、`studio.js`（319 行，器识/便签/沉淀报告）、`playground.js`（209 行，试炼场）。`terminal.js` 同步补 `deactivateTerminal`（切走视图时停掉轮询）。
  - **特性开关**（`core/settings.py`）：`FEATURE_WIKI` / `FEATURE_TERMINAL` / `FEATURE_WORK_ITEMS` / `FEATURE_CRYSTALS` 默认 `False → True`，且 `EXT_ROUTERS` 的 `crystals` 从硬编码 `(False, router)` 改为跟随 `settings.FEATURE_CRYSTALS`——**此前该开关对 crystals 完全无效**（写死 False），是死开关。
  - **冲突裁决改 JSON body**（ADR-0010）：`POST /conflicts/{id}/resolve` 的 `decision`/`note` 从 query 参数改为 body（新增 `schemas.ConflictResolveReq`）。原因：中文长理由进 URL query 会 414 且写进访问日志。**这是破坏性 API 变更**，前端 `api.js` 已同步。
  - **监控面板**：`/health/stats` 增 `by_domain` 分组统计；导航加 `aria-current="page"` 无障碍标记。
  - **d3 本地兜底**：`ui/d3.v7.min.js`（v7.9.0，279KB）入库，CDN 挂掉时 `document.write` 回退；`routes_ui.py` 资源白名单同步放行 5 个新文件。
  - **测试增量**：`tests/test_console_api_contracts.py` 14 例（不 mock，真实起 FastAPI TestClient 打契约）——覆盖 stats 分组、冲突裁决 body 化与旧 query 拒绝、crystals 路由挂载、资源白名单、limbo 过滤、图展开等。

### Changed

- **提案 apply 全链补归属——LLM 回一个别人的记忆 id，A 再也改不动 B 的记忆（2026-09-28，票据 `.scratch/proposal-apply-gaps/issues/16-proposal-apply-no-owner.md`）**：
  - **先说影响**：`apply_proposal` **一个身份都不取**，而它的三个寻址字段**全部来自 LLM 输出**——`target_memory_id` / `evidence_ids` / `proposed_patch["key"]`。curator 只要回一个别人的记忆 id，apply 就按主键直读然后**改写它**：deprecate 是归档、merge 证据环是**从 FTS 与向量库除名**、consolidation 是折叠。**这些都没有 undo 入口**（`rollback` 只回滚内容，不回滚删除），也没有 audit 事件。复现实证（`.scratch/proposal-apply-gaps/probe_16_cross_user_apply.py`，子进程隔离，五个场景）：① update target=B → `B.content` 被整条覆盖；② merge evidence=[B] → `B.status=archived` + **向量库 delete(B)**；③ deprecate target=B → 归档；④ update key 回退只命中 B → 覆盖；⑤ consolidation evidence 混合 → B 被折叠。**五个场景全部成功改写 B 的行，无一拒绝。**
  - **可达性已被实证，不需要 A 主动调用**：`evolve_worker.run_evolve_once:41` / `run_pending_proposals:59` 选候选与提案**不带归属过滤**，`reflector._run_reflect_once:513` 同。**定时任务会替 A 把提案 apply 到 B 的记忆上。**
  - **修法一：apply 边界一道硬门，而不是逐个 `session.get` 后补判定**。`apply_proposal(proposal_id, principal=None)` 取到 prop 后、**动手之前**（全链唯一"还没写任何东西"的时刻）一次性解析全部目标 id 并统一校验归属，任一条不属于即**整体**拒绝。逐条补判定的后果：merge 分支改了三个目标，漏掉 evidence 环那条照样能删别人的记忆——**票 14 的 rejecter 就是这么漏的**；或者先把自己的应用了、再把别人的跳过了——**半 apply 比不 apply 更脏**（A 的记忆已被折叠且没有 undo）。
  - **修法二：worker 也要带身份**。`run_evolve_once` / `run_pending_proposals` 的候选集与提案集加 scope（口径同票 12 的 `_kaogong_scope`：`user_id == viewer OR IS NULL`，NULL 老行放行——真实库 636/657 行是 NULL 属主，判不可见会让单人部署整体空转）。**否则路由层修得再好，定时任务照样跨用户 apply。**
  - **修法三：提案生成侧同批修**——LLM 输出是**不可信输入**，与 HTTP 参数同级对待。`proposer._resolve_target_id` 按归属收窄 key 解析（只命中 B 的记忆时整条丢弃，不落提案）；`reflector.propose_from_reflection` 的 evidence 从逐条 `session.get` 存在性校验改为 `id.in_` + scope（后者正是票 14 踩过两次的盲区：主键直读绕开 SQL 层 scope），target 补 `_reflect_owns` 行级判定。`evolution_service.decide_proposal` 的 approve 分支下传 principal（approve 会直接 apply_proposal 写库）。
  - **宁 miss 不脏写**：校验不过 → 提案落终态 REJECTED + `decision_reason` 留痕（含"几条目标里几条属于别人"），不静默丢弃、不自动改成指向别处、不降级为 add。落终态而非早退是必须的——`run_pending_proposals` 专捞 APPROVED，只早退不改状态会每轮重试每次失败＝livelock（同 ADR-0050 决策 3）。
  - **变异验证 10/10 全杀**（`.scratch/proposal-apply-gaps/mutation_check_16.py`，子进程隔离 + timeout + atexit 还原）。第一轮 **3 条 MISSED**，逐条诊断全是真实断言缺口而非等价变异：M6（worker scope 改回 None）——测试只断言"B 的记忆没被改"，而 apply 边界硬门本来就会拒它，**硬门兜住了测试的漏洞**；M9（reflector target 不判归属）——测试里 target 用的是 A 自己的记忆，那个分支从未被求值；M10（approve 不下传 principal）——根本没有测试走 `decide_proposal`。分别补上 spy 观察"实际执行了哪些提案 id"、一条 target 指向 B 的用例、一条 `decide_proposal(approve=True)` 的用例后才全杀。
  - **测试增量 17 例**（`tests/test_proposal_apply_ownership.py`，全部不 mock：真实内存 SQLite + 真 `init_fts` + `patch db.get_session`，替身只覆盖外部边界）。覆盖 target / evidence / key 回退三条寻址路径各自指向 B、混合目标整体拒绝（含"自己的那条也不得被折叠"）、自己的提案照常 apply、NULL 属主老行照常、admin 与 `principal=None` 全量、worker 两条可达路径、生成侧三处过滤、以及 service approve 下传。全量 pytest **1796 passed / 0 failed**。

- **迁移链中途失败不再静默跳过后续全部迁移——坏了现在 `/health/deep` 看得见（2026-09-28，票据 `.scratch/migration-chain/issues/01-chain-break-silent-skip.md`）**：
  - **先说影响**：`apply_migrations` 里 25 个迁移块，**任何一块抛异常，它后面所有迁移都不跑**——但服务照常启动，日志里只有一行 error。实证（`.scratch/readside-gaps/probe_24_chain_break.py`，子进程隔离）：在 v9 块收尾前注入一次异常 → `version` 停在 **8**、只有 4 张表，`reflect_run`（v10）/`session_checkpoint`（v12）/`memorynode.user_id`（v26）**全部缺席**。之后每次查新表都是 `no such table` / `no such column`，服务"起来了但半边是坏的"。瞬时失败（锁竞争）下次启动会自愈；**持久失败（DDL 写错、表被外部改坏）永久卡死且无任何信号**。
  - **根因**：`PRAGMA user_version = N` 写在**块尾**，抛在它之前 version 就停在 N-1，后面所有 `if user_version < M` 全部不成立；外层 `except` 只 `logger.error` 一行，不记失败位置、不暴露给健康检查。25 块里 19 块连内层保护都没有。
  - **修法**（三条同时成立，不改"失败不阻断启动"的既定行为）：① 迁移链拆到 `lantai/storage/migrations.py`，25 个块变成 `_migrate_vN(conn)` + `MIGRATIONS` 表驱动，**逐块 try/except**——一块失败不影响后面的块，version 只在块真成功后推进（失败的跳下次启动重试）；② **单一版本真源** `TARGET_SCHEMA_VERSION`（原来 `db.py` 自己硬编码一份 26、迁移链里又散落 25 个 `if user_version < N`，两处真相改版本时漏改哪边都不报错）；③ `/health/deep` 新增 `checks["migrations"]`，version 没到目标或有失败记录时报 `fail` 并附上失败跳与原因——同 `fts-availability/01` 的形状（sqlite 能连、chromadb 能读都探不出迁移没跑完，缺表缺列只在用到的那一刻才炸）。
  - **等价性验证先行**（`.scratch/migration-chain/verify_equiv.py`）：重构前后在 5 个起始版本（0/1/8/13/25）上各跑一遍，比对 `user_version` + 42 张表的列集合 + 130 个索引——**全部一致**。过程中抓到两个真 bug：循环里的 version **局部快照**不推进（pragma 推进了但判断仍用初始值），以及 `_has_column` 随拆分移走却漏改 `migrations_v022.py` 与 `tests/test_ownership_migration.py` 的导入路径（后者正是"宁 miss 不脏写"对重构同样成立的例子——`db.py` 保留兼容再导出）。
  - **变异验证 7/7 全杀**（`.scratch/migration-chain/mutation_check_01.py`，子进程隔离 + timeout + atexit 还原）。第一轮 3 条 MISSED，逐条定性：M8 是真实的断言缺口（health 的 `if failures:` 分支从没被求值过——缺"有失败记录时调 health"这条输入），已补测试杀掉；M2（快照不推进）经实证是**等价变异**——`>=` 用旧值只会让块重复执行而非跳过，25 个块全幂等故结果完全相同，已从清单移除并写明理由，没去写一条自欺欺人的测试。
  - **测试增量 11 例**（`tests/test_migration_chain_isolation.py`，全部不 mock：真实临时 SQLite 库 + 真实 `apply_migrations` + 真实 `health_deep`）。覆盖逐块隔离、失败记录带版本号与原因、失败块不推进 version（用 spy 记录 pragma 写入序列——光看最终值杀不掉这个变异）、从中间版本起跑能补到目标、health 的两个 fail 分支与 ok 分支、以及"失败不阻断启动"这条硬约束。全量 pytest **1779 passed / 0 failed**。

- **边读侧补归属——A 不再能列出 B 的记忆关系图与取代链（2026-09-28，票据 `.scratch/readside-gaps/issues/22-edges-list-chain-readside-no-identity.md`）**：
  - **先说影响**：`GET /edges/{memory_id}` 与 `GET /edges/{memory_id}/supersed-chain` **一个身份都不取**。`routes_edges.py` 四条路由里 `POST /edges` 与 `DELETE /edges/{id}` 都取了身份并校验，只有这两条读路由漏了——同票 19/20 的形状「同一个文件里修了一条、漏了旁边那条」。A 拿 B 的 memory_id 就能列出 B 每条边的 id/source/target/relation/confidence；supersedes 链更值钱：它直接告诉 A「B 的这条记忆被谁取代了」，`superseded_by` 就是下一步该读哪条——**一条链把 B 的整条取代路径摊开**。
  - **归属必须按端点记忆判，不按边自身判**（实测推翻票面最初设想，`.scratch/readside-gaps/probe_22_edge_owner_dist.py`）：真实库 **76/76 条边 `user_id` 全 NULL**。按边自身过滤对全库一条都不生效——修了等于没修。归属信息只在端点记忆上，口径同票 17 的 `graph_retriever._owns`：两端属主 `NULL`（历史行）或等于 viewer 才放行，**两端有一个不可见就整条隐藏**（露出一半等于告诉 A「B 有条边连到某个 id」）。
  - **顺手抓到一处静默失效：terminal 两处边推送是死代码**（`.scratch/readside-gaps/probe_22_*.py`）：`routes_terminal.py:132` / `:200` 写 `for e in node_edges if isinstance(node_edges, list) else []`，而 `list_edges` 返回的是 **dict** `{"edges": [...]}`——`isinstance` 恒 False，**两个循环体一次都没执行过**。terminal 的「正在加载记忆关系图谱」那步一条边都没送出过，却照样 yield 一个空 `{"edges": []}`。用户看到空图谱，代码看起来在跑。已改成 `.get("edges") or []` 并下传 `principal`，两处一起修（只修一处等于修活一半）。
  - **真实库另有一层脏数据**：74/76 条边的 `source_memory_id` 是 `doc_*`（`evolution/promoter.py:487` 的 `evidence_ids` 里混着文档 id），在 `memoryitem` 里**不存在**。这类边判不可见——露出幽灵 id 没有意义，而 id 本身是票 19 记录过的攻击材料。**不回溯补 76 条边的属主**（宁 miss 不脏写）。
  - **变异验证 11/11 全杀**（`.scratch/readside-gaps/mutation_check_22.py`，子进程隔离 + timeout + atexit 还原）。第一轮 **5 条 MISSED**，逐条定性全部是「测试输入形状让条件永远不求值」而非覆盖缺口：M5 只查一端点（测试里 target 端从不构成判据）、M8/M9 chain 路由不过滤（用例里 B 不是任何边的 source，链本来就空）、M10/M11 terminal（nodes 恒空，spy 从未被调用）。补了「source 是 A / target 是 B」与「target 是 A / source 是 B」两条对偶、一条 B 自己的两跳非空链、以及 patch `hybrid_search` 让 nodes 非空后才全杀。
  - **测试增量 17 例**（`tests/test_edges_readside_ownership.py` 11 + `tests/test_terminal_edges_deadcode.py` 4，另有 2 例来自票 23 同批跑），全部不 mock：真实 in-memory SQLite + `init_fts` + `patch db.get_session`。全量 pytest **1768 passed / 0 failed**。

- **supersedes 取代链修自指环——`as_target` 默认值吃掉调用方意图，链尾不再出现「自己被自己取代」（2026-09-28，票据 `.scratch/readside-gaps/issues/23-supersed-chain-self-loop.md`）**：
  - **先说影响**：`GET /edges/{memory_id}/supersed-chain` 会返回**错的链**。`get_supersed_chain` 只想沿 source 方向走（`current` 是被取代的旧值，边指向新值），但 `get_edges` 的 `as_target` **默认 True**，两个都 True 就让查询走 OR 分支——每一步都把「指向 current 的边」也取回来，于是链尾出现 `superseded_by == memory_id` 的自指环，且让链**多跳一步**。`visited` 集合只挡住了无限循环，挡不住**已经追加的假条目**，所以不崩溃、只是静默给出错数据。
  - **真实库实证**（`.scratch/readside-gaps/probe_22_exploit.py`）：`get_chain('mem_01KZRNAJHTXJQCBJ8K10TBHRCQ')` 返回一条 `superseded_by` 等于自身的链，而 `source == 该 id` 的 supersedes 边实测 **0 条**——这条链根本不该存在。3 条 supersedes 边里 2 条中招。
  - **修法是显式 `as_source=True, as_target=False`，不改 `get_edges` 的默认值**：默认值没错，错的是调用处依赖了一个会反转语义的默认。改默认值会连带改动 `list_edges`（`/edges/{id}` 的语义是「进出都要」）与 terminal 两处，把一次定向修复变成全签名重构。
  - **影响面已核实只有这一个端点**：`hybrid.py:248` 的 `_edge_cb` 用自己的查询（source 与 target 都在候选集里），不走 `get_edges`，supersedes 检索排序未受影响；全仓 `get_supersed_chain` 只有这一条调用路径。
  - **变异验证 6/6 全杀**（`.scratch/readside-gaps/mutation_check_23.py`，子进程隔离）：M1 还原 bug、M2 方向写反、M3 两方向都查、M4 取 `edges[-1]`、M5 拆掉 `visited`、M6 去掉 relation 过滤。其中 M4/M5/M6 第一轮 **MISSED**——原因是测试输入形状让变异等价（每步只有一条边时 `edges[0]` 与 `edges[-1]` 恒等；数据里没有 supports 边；没有环），**不是覆盖缺口**。补了「同 source 多条出边」「supports 边不得进链」「A↔B 环必须两跳后停」三条后全杀。M5 是被**超时拦下死循环**杀掉的——测试挂死同样是「没放过它」，所以变异脚本必须带 timeout（上一轮没带，脚本被外部杀掉，被测文件被留在变异状态，靠 `atexit` 还原才救回来）。
  - **测试增量 8 例**（`tests/test_supersed_chain_selfloop.py`，全部不 mock：真实 in-memory SQLite + `init_fts` + 直调 `get_supersed_chain`）。Red 阶段失败信息精确显示假尾巴 `('mem-C', 'mem-C')`。全量 pytest **1751 passed / 0 failed**（+8 = 本票新测试）。

- **CI 格式门禁二次失守收口 + `release_check` 加 lint 自查——全量测试不再被 lint 静默拦停（2026-09-28，票据 `.scratch/readside-gaps/issues/21-ci-format-gate-regression.md`）**：
  - **先说影响**：`.github/workflows/tests.yml` 的 test job 里 lint 步骤**排在**全量 pytest 与遗忘质量门禁**前面**。`ruff format --check` 自 `6c07da7d` 起一直红，累积 9 个不合规文件，所以最近 5 个提交的 CI 上**全量测试和遗忘门禁一步都没跑过**——「本地 pytest 全绿」从来不等于「CI 绿」。这是记忆里 `ci-lint-blocks-test-gate` 那条教训的二次发作（上次是 `ruff check` 的 73 条违规，修于 `.scratch/ci-lint-gate/`）。
  - **逐提交实测定位，不靠推理**：用**干净 worktree**（`git worktree add --detach`）逐个提交跑 `ruff format --check`，差分得出每个提交单独引入的违规——`6c07da7d` 1 个文件（就此开始红）、`fda5cbd6` +3、`9964a343` +3、`622da8ef` +2，累积到 9。**教训：脏工作区上 `git checkout` 会静默失败**，第一轮我就在脏工作区里 bisect，得到 5 个提交主题全同、数字全错的结论。
  - **根因不是「又脏了」，是缺一个提交前自查**：上一轮收口时门禁是绿的（`be0f407e` 实测 0 违规），之后 12 个提交里 4 个把格式带回红。**一次性收口不等于长期收口。** 所以本票除了修文件，把自查做成了自动的。
  - **`scripts/release_check.py` 新增 `lint_gate_issues()`**：跑 `ruff check` + `ruff format --check`，且**排在版本一致性检查之前**——lint 红 = CI 的全量测试一步不跑，这时查版本/tag 没有意义。`_find_ruff()` 三级回退：本仓 `.venv` → 系统 PATH → **当前解释器同目录**（第三级专为测试造的 tmp_path 准备；第一版只查 `repo_root/.venv`，在 tmp_path 下必然 SKIP，三条新测试两条假红）。
  - **自查函数当场抓到自己**：写完第一次跑 `release_check.py`，它报的是**我刚写的那段 `subprocess.run` 没 format**。这正是它该有的行为——先写后格式化会漏，提交前跑一遍不会。
  - **「纯风格」用 AST 验，不用 `tr -d` 验**：`.scratch/readside-gaps/verify_21_style_only.py` 把源码解析成语法树、丢掉全部位置属性再序列化，只有**词法与结构**变化才会显形。第一版用 `tr -d '[:space:]'` 比对，分不清「删了一个空行」和「删了换行」，把任何变化都判成实质变化——**判据本身要先验过再用**。结果 8/9 AST 完全一致；`memory_service.py` 不一致是**预期的**（含票 20 的真实逻辑改动，`git diff` 逐行确认）。
  - **测试增量 3 例**（`tests/test_release_check.py`，全部真跑 ruff 不 mock）：`test_lint_gate_passes_on_real_repo` 是**回归哨兵**（任何人提交了没 format 过的代码，这里立刻红，不用等 CI）；`test_lint_gate_catches_unformatted_file` 是**决定性**的——造一个必然不合规的文件断言 issues 非空，没有这条，自查函数可以是恒空返回 `[]` 的摆设；`test_lint_gate_catches_lint_violation` 锁 `ruff check` 维度。
  - **验收**：`ruff check` → All checks passed；`ruff format --check` → **388 files already formatted**（修复前 9 files would be reformatted）；`release_check.py` → `[PASS] CI lint 门禁`；全量 pytest **1743 passed / 0 failed**（+3 = 本票新测试）。

### Security

- **Obsidian 同步补身份 + verbatim 去重补归属 + 实体/边补归属——A 的哈希碰撞不再污染 B 的图邻域（2026-09-28，票据 `.scratch/readside-gaps/issues/20-obsidian-sync-verbatim-dedup-no-owner.md`）**：
  - **先说影响**：`POST /obsidian/sync` **一个身份都不取**——`EXT_ROUTER` 带 `dependencies=AUTH` 所以认证了某人，却把身份丢掉了（同文件的 `/verbatim/search` 本来就 `Depends(get_current_user)`，只有这一条漏了，形状同票 19「同一个文件里一条修了一条没修」）。而 `add_raw_memory` 的 verbatim 去重是 `memory_type + key + status` 三条件、**无任何归属过滤**。两者叠起来：A 提交一段与 B 的 verbatim 内容 sha256 相同的文本（读同一篇公开文档即可构造），返回的就是 **B 的** `memory_id`——**A 拿到别人的记忆 ULID**，而 ULID 是票 15/16/17 都需要的前置预言机；`sync_obsidian_note` 还会拿这个 id 取到 B 的行，于是 **A 的双链实体名从 B 的记忆行连出去**，污染 B 的图邻域，再经 `expand_graph_associations` 影响 B 自己的召回。
  - **票面把方向写反了，实测严重度低一档**：`_link(note.id, ent.id)` 的方向是「**笔记 → 实体**」，不是「笔记 → B 的记忆」。所以第一次 sync 时 `note` 就是 B 的记忆行，边从 B 的记忆**连出去**指向实体——场景 1 的 `links_created=2` 是笔记→实体那两条，票面把它们当成了笔记→B。真正的污染在**第二次** sync：B 的行成了 `note`，A 的实体名从 B 的记忆连出去。第一版探针查 `target=B` 得出「没污染」的错结论，改成查 `source=B` 才看见。
  - **票面「根治点在票 17」是错的，实测票 17 挡不住**（`.scratch/readside-gaps/probe_round14b.py`）：A 两次 sync 后，**B 自己** `expand_graph_associations(["v-B"])` 照样展开出 A 的实体。根因不是边——`_get_or_create_entity` 造实体时**一个归属列都不填**，实体行 `user_id` 恒 NULL，`_owns` 判「NULL 属主可见」就放行。**污染通道是「无主实体 + NULL 可见口径」**：票 17 修的是「沿**别人的**边走」，这里是「沿**自己的**边走到**无主**实体」，两回事。所以修法必须加第三条：实体行与边都落 `user_id` / `tenant_id` / `agent_id`。
  - **NULL 属主 verbatim 判「重复」不判「新建」，理由实测**：真实库 **391 条 verbatim 全部 `user_id` 为 NULL**。verbatim 是**内容寻址**（sha256 即 id 语义），同一段文本就是同一条记忆，属主只是标注、不是身份判据。判「新建」会让每一条既有无主 verbatim 都无法去重，同一内容每 sync 一次就多存一份——**把内容寻址退化成多份存储**。这不是理论选项，是数据现状决定的。
  - **实体/边的幂等查重不按属主**：实体是全局图谱节点（同名 `[[链接]]` 在全库是同一个概念），按属主过滤会让同一实体名在每人名下各建一份、把图谱拆碎；边同理。既有无主实体行继续可复用，**不回溯补属主**——补属主要猜「原本属于谁」，猜错比留 NULL 更脏（宁 miss 不脏写）。
  - **实施中发现一处票面没写的形状问题：先写后拒**（`.scratch/readside-gaps/probe_m5_lane.py` 实测）：`ensure_can_delete` 同时查 lane，而它在 `sync_obsidian_note` 里排在 `add_raw_memory` **之后**——越 lane 请求返回 403，可 verbatim 行**已经落库**。「调用方以为失败了，数据却留下了」。所以 lane 校验**前移到路由层**（同 `routes_memory.add_raw_memory_route` 第一行），service 里那条留作纵深防御。
  - **`allowed_lanes is None` 是「未绑定、不限泳道」，不是「零泳道」**（`acl.ensure_can_delete` 同口径）：写成 `req.lane not in (ctx.allowed_lanes or [])` 会把所有未绑定主体（含 worker/脚本）全拒。
  - **`add_raw_memory` 的归属四元组必须一起传**：它的形参默认 `user_id="default"`，不传的话 A 建的 verbatim 行属主恒为 "default"，下一步的 `ensure_can_delete` 会把 **A 自己刚建的笔记**拒掉（M9 变异实测，5 个测试红）。
  - **M5 第一次 MISSED，靠穷举 + spy 定性，不是靠推理**：`probe_m5_reachability.py` 装 spy 到 `acl.ensure_can_delete` 上跑 6 种形状，实证 **user 维度结构不可达**——M1 的去重归属收窄已堵死「A 拿到 B 的非空属主行」，guard 拿到的 `resource_user_id` 只会是 NULL 或自己的 id。**不是覆盖缺口**。但 lane 维度可达，于是补了两条测试锁它（含一条对偶用例：少了它，把条件写成 `lane not in lanes` 也能让越权那条绿）。教训同票 19 的 M6/M7：**一条判断若在测试的输入形状下永远不求值，它对应的变异就杀不掉；而「结构不可达」不等于「可以删」。**
  - **测试增量**：`tests/test_obsidian_sync_ownership.py` 13 例。Red 1 决定性断言落在返回的 `note_id` **字段值**上（不只看「没建边」——没建边可能因为别的原因）；Red 2b 决定性锁「B 自己的图检索不展开出 A 的实体」；Red 6 路由层含「403 **且零落库**」双断言。**变异验证 11/11 全杀**（`.scratch/readside-gaps/mutation_check_20.py`，子进程隔离）。全量 pytest **1743 passed / 0 failed**。

- **检查点历史补归属——A 拉不走 B 的记忆全部历史版本（2026-09-28，票据 `.scratch/readside-gaps/issues/19-checkpoint-history-no-owner.md`）**：
  - **先说影响**：`routes_checkpoint.py` 的 handler **取了身份却没往下传**——`session_id` 分支传了 `principal=ctx`（票 09 修的），`memory_id` 分支没有。`evolution_service.list_checkpoints(memory_id, limit)` 按 `memory_id` 直查全表，无任何归属过滤，把每个 checkpoint 的 `model_dump(mode="json")` 全量吐出。而 `MemoryCheckpoint.before` / `.after` 是**完整行快照**（`content` / `title` / `structure` 全在里面），所以 **A 调 `GET /checkpoint?memory_id=<B 的记忆 id>` 就能拿到 B 这条记忆的每一次变更前后全文**。这比读当前版本更糟：当前版本可能已经被改过，**历史版本不会**。而 checkpoint 是回滚的原料——先拉历史、再挑一个版本回滚（票 13 已修 rollback 的归属，读侧这道口子一直开着）。探针实证：A 拉 B 的记忆，2 条 checkpoint 的 `before`/`after` 全文到手。
  - **票面优先级是反的，改选 join 而非「先取记忆行再过 `ensure_can_delete`」**：`consolidation_service.py:357` 故意写 `memory_id="cluster_consolidation"` 这个**伪 id** 做沉潜留痕对账键，它**没有 `MemoryItem` 行**。「先取记忆行」在它身上必然拿到 None，此时 403（把「没有这条记忆」说成越权）、404（改变响应形状）、放行（泄漏）三个答案全是错的——**这是该方案的结构性缺陷，不是实现细节**。join 天然处理两类无主行：伪 id 与**孤儿 checkpoint**（`delete_memory` 不级联删 checkpoint，历史比行活得久），匹配不上就不返回。对非 admin 这正是「宁 miss 不脏写」：没有属主可判，放行等于把已删记忆的编年史敞开。
  - **同一个 handler 两条分支、一条修了一条没修**——正是「以为修完了」的典型形状。已在路由写明：本分支管记忆历史，`session_id` 分支管会话底本，别让后来人以为重复。
  - **不需要第二道行级校验**：票 17「边决定走哪条路、行决定露出什么」的两层结构在这里**塌缩成一层**——归属判定就在 SQL 的 join 条件里，读路径上**没有** `session.get(MemoryItem, …)` 这种按主键绕开 scope 的直读（票 14 踩过两次的盲区）。别照票 17 的样子再补一个 `_owns`，那是重复判据。
  - **全库第一次给读侧加租户维度，口径写清**：「**双方都非空且不同才挡**」，同写侧 `ensure_can_delete`。不能写成严格 `tenant_id == viewer_tenant`——`auth.py:143` 的 tenant 是**客户端 header 自报**的，没有租户注册表、没有校验，严格相等会让同一个人**不带 header** 时连自己 NULL 租户的老行都看不见（`.scratch/readside-gaps/probe_tenant_semantics.py` 实证：`X-Tenant-Id=None` 时 m-1 凭空消失）。NULL 老行一律照旧可见。
  - **读侧收窄静默返回空，不译 403**：同票 09 `get_checkpoint` 返回 None 的形状。admin 与 `principal=None`（worker/CLI/MCP）**连 join 都不写**，所以孤儿 checkpoint 与伪 id 对它们照见——沉潜对账与运维排查看的就是这两类。
  - **测试增量**：`tests/test_checkpoint_history_ownership.py` 23 例，6 个测试类。决定性断言落在 `checkpoints[*].after.content` / `before.content` 上，不只看列表长度。含反向用例保功能没被修废（A 自己的历史照常全返、NULL 属主老记忆照常可见、admin 与 `principal=None` 照见全部含伪 id 与孤儿、同租户照常可见、`limit`/倒序/返回形状不变），双分支回归（票 09 的 `session_id` 用例继续通过、且**仍然**挡别人），HTTP 层与 service 层两条出口，不 mock 冒烟两条。
  - **两个 MISSED 都是真缺口，不是等价变异**（`.scratch/readside-gaps/probe_m6_m7_diff.py` 逐个实证）：**M6**（租户严格相等）——我第一版用例用的是「**不带** header」的主体，而 M6 在那种主体下 `viewer_tenant` 本就是 None、条件整段不生效，**用例根本测不到它**；真正能区分的形状是「header 非空 + 行租户为 NULL」。**M7**（`viewer_tenant or "__never__"` 恒生效）——非 admin 且不带 header 的主体连自己**有租户标签**的行都看不见，而 NULL 用例靠 `IS NULL` 侥幸漏过、杀不掉它。教训：**给一个条件写用例时，要问「这个条件在什么输入下真的会求值」**。
  - **变异验证 13/13 全杀**（`.scratch/readside-gaps/mutation_check_19.py`，子进程隔离）：含 M13「改回票面原优先项」，3 个测试红。全量 pytest **1727 passed / 0 failed**（基线 1705）。

- **级联删除补归属——A 删自己的文档，B 的记忆不再被连带物理删除（2026-09-28，票据 `.scratch/readside-gaps/issues/18-delete-document-cascade-no-owner.md`）**：
  - **先说影响**：`promoter.delete_memory` 没有任何 principal 形参，`s.get(MemoryItem, id)` 取到就 `s.delete`，再 `sync_fts(None)` + `delete_memory_item` 从 FTS 和向量库除名——**这是全代码库最具破坏性的操作**。它的调用方 `source_service.delete_document` 逐个删除「无更多 doc 来源」的目标记忆，**不检查这个记忆属于谁**。而 `DELETE /documents/{id}` 确实调了 `ensure_can_delete`，但只校验 `RawDocument` 那一行——**边的另一端是另一张表的另一行，路由那道校验够不着它**。探针实证：A 删自己的文档，B 的记忆**行没了、FTS 索引也没了**，无 checkpoint、无 undo、无 audit 事件。这是前 18 票里第一个**物理删除**型越权。
  - **结构性重排，不是加个 if**：原实现在第 4 步就 `s.commit()`（文档、分块、候选、边全部落库），第 5 步才发现目标记忆不属于 A——那时「整体中止」已不可能，半途删一半比不删更脏。本票把归属校验从删除中途**提到任何删除发生之前**，越权即整体中止，一条都不删。
  - **纵深防御第二道校验不是装饰**（探针实证）：第 3 步预校验对 `mem is None` 的目标跳过，第 5 步仍会调 `delete_memory`。把预校验的 `s.get` 对 B 蒙混成 None 后，`delete_memory` 那道校验就是**唯一防线**。若 `delete_document` 不把 principal 传下去，B 的记忆会被直接删掉。
  - **「整体中止」必须连文档一起中止**：第一版断言只查记忆行，一个「先 commit 删文档和边、再校验」的实现能全绿混过——文档和边已经没了。已补文档行 + 边条数断言（D7 变异教会我的）。
  - **两道校验管的不是同一行**，已在路由 docstring 里写明：本路由这道管 `RawDocument` 行（票 04 既有），service 那道管**级联目标记忆行**。别让后来人以为重复就删掉其中一道。
  - **`delete_memory` 另补 `if not mem` 分支**：原实现行不存在也返回 `ok: True`——`delete_document` 靠 `"missing"` 字符串决定是否中止，计数会虚高、中止判断会被绕过。
  - **403 在路由边界翻译**：service 的 `ok: False` 此前以 200 + ok:false 返回给 HTTP 客户端，语义上成功、实际失败（同 `routes_evolution._ok_or_raise` 范式）。forbidden → 403，其他 → 404。
  - **测试增量**：`tests/test_document_cascade_ownership.py` 15 例，4 个测试类。决定性断言落在**落库行 + FTS 索引 + 向量库除名记录**三处——删索引比删行更难察觉。含反向用例保功能没被修废（A 自己的级联照常删干净、NULL 属主老行照常可删、孤儿边不当中途失败、admin 与 `principal=None` 照常删、跨租户目标被挡），不 mock 冒烟一条。
  - **顺带修掉一处测试自身的假绿机器**：`tests/test_document_cascade.py` 的 fixture 用 `allowed_lanes=[]`——`[]` 是「绑定了但一条 lane 都不给」，`ensure_can_delete` 的 lane 检查会挡掉**所有**删除。该文件测的是级联语义不是泳道 ACL，改为 `None`（未绑定）。已用 `git checkout` 还原源文件后单独验证：**仅改 fixture、源码保持原样，那 2 个测试照旧通过**——证明 fixture 修正是正交的，不是为了让新代码绿。
  - **变异验证 13/13 全杀**（`.scratch/readside-gaps/mutation_check_18.py`，子进程隔离）。第一轮 **6 个 MISSED**：D3 锚点撞了 `rollback` 的同名分支（`replace(...,1)` 命中错的那个）、D7/D8 是真实缺口、D4 是锚点缩进不匹配、D13 与另一条是**等价变异**（`ensure_can_delete:127` 本就对 admin 早退；`promoter.delete_memory` 自己也传 `resource_tenant_id`，双重校验下去掉一处行为不变）。全量 pytest **1705 passed / 0 failed**（基线 1701）。
  - **踩坑**：一次 `git checkout <file>` 把刚写完的实现整个还原了（19 处 principal → 15 处），已按编辑记录完整重写并复验。

- **贯珠图检索补归属——A 的 `/search/graph_expand` 不再沿别人的边展开出 B 的记忆正文（2026-09-28，票据 `.scratch/readside-gaps/issues/17-graph-edges-no-owner-filter.md`）**：
  - **先说影响**：`MemoryEdge` **有**归属四元组（`create_edge` 会填），但**没有任何一条查询按归属过滤边**。`expand_graph_associations` 的边查询一个条件都不带——于是**种子集明明已按 principal 收窄，边这一层照样跨用户**：任何一条从 A 的记忆指向 B 的记忆的边（A 自己建的、B 自己建的、票 16 的无归属 `apply` 造的 `supersedes`、票 18 的 obsidian 造的 `links` 都算）都让 B 的记忆成为 A 种子的「邻居」。随后 `s.get(MemoryItem, neighbor_id)` 按主键直读，**`neighbor_item.content` 原样进 `associated_memories` 返回给 A**。`min_edge_conf=0.5` 轻易满足，两跳足够。此前唯一的过滤是 `allowed_lanes`——那是**泳道**检查不是**归属**检查，A 和 B 通常共用默认泳道集。
  - **两处都要修**（边决定走哪条路，行决定露出什么）：边查询加 `_edge_scope(principal)`，`s.get(MemoryItem, neighbor_id)` 取到行后补 `_owns` 判定。`session.get` 按主键直读、绕开 SQL 层 scope，是票 14 踩过两次的盲区。
  - **差分探针实测推翻了票据里的一句想当然**（`.scratch/readside-gaps/probe_edge_scope_leak.py`）：穷举 3^3 种邻居行属主 × 3^3 种边属主共 729 种形状，把 `_edge_scope` 打回恒 `None` 后——**正文泄露形状 0 种**（行层 `_owns` 一个人挡住了全部跨用户正文，因为被拒的邻居在 `queue.append` 之前就 `continue`，B 的行永远不会变成 `curr_id` 去带出下一跳），**遍历范围有差异 266 种**。所以边层是**纵深防御**而非唯一防线，其可观测价值是**收窄遍历**：没有它，A 会沿着不属于自己的边走下去，把本该止步的图走到第三跳。测试据此锁**遍历边界**而不是「B 的正文没出现」——**初版 7 个测试全部锁错了对象，边层三个变异一个都杀不掉（3/13 MISSED）**。
  - **为防将来重排代码埋了护栏**：`TestGraphRejectedNeighborNeverBecomesNextHop` 锁住「先判归属、后入队」这个顺序。行层之所以一个人就够用，全靠 `queue.append` 在 `_owns` 判定之后；若有人把它挪到前面，行层会瞬间不够用，那条测试立刻红。票据的「只滤行不滤边也会漏」在当前结构下不成立，但**成立与否依赖代码顺序**——这正是护栏存在的原因。
  - **NULL 属主老边必须可通行**：口径同前 14 票（`user_id == viewer OR IS NULL`）。真实库的边绝大多数是迁移前的历史行，判不可见会让整张图在单人部署下断连。
  - **principal 一路下传**：`graph_augmented_search` 要传给**两处**——`hybrid_search` 与 `expand_graph_associations`，此前一处都不传：初筛结果本身就没收窄（票 15 的 `vector_owner_filter` 到不了这里），种子集带着别人的记忆，图那一层再漏一次。路由 `/search/graph_expand` 补 `principal=ctx`（此前只取 `ctx.allowed_lanes`，人本身没往下传）；MCP 复用票 10 的 `_principal_from_params`——宿主透传 `user_id` 才构造 principal，否则留 `None`，**不猜身份**。
  - **测试增量**：`tests/test_graph_ownership.py` 22 例，5 个测试类。含多条反向用例保功能没被修废（A 自己的两跳照常展开、NULL 属主老边与老行照常可通行、泳道过滤照常生效、admin 与 `principal=None` 全图），路由层与 MCP 两条出口复现，不 mock 冒烟一条。
  - **变异验证 13/13 全杀**（`.scratch/readside-gaps/mutation_check_17.py`，子进程隔离）：边层 scope 四条、行层三条、形参链四条、路由层与 MCP 各一条。全量 pytest **1690 passed / 0 failed**（基线 1668）。
  - **票据第 4 条已核实无需改动**：`hybrid.py:248-253` 的 `_edge_cb` 只取边的 `relation`/`confidence` 做排序加分，不读记忆正文、不返回邻居行，无泄露面。`record_ops_service.py:448` / `source_service.py:161-163` 两处边查询已在票 14/16 的归属审计中处理，不重复。

- **向量检索补归属过滤——A 的一条 `/add` 不再把 B 的记忆正文送进外部 LLM，也不再改写 B 的 `importance`（2026-09-28，票据 `.scratch/readside-gaps/issues/15-vector-search-no-owner-filter.md`）**：
  - **先说影响**：两处向量检索一个过滤都不带，返回的是**全库最近邻**——`memory_service._apply_dedup` 与 `gate/decision.py`。A 控制新记忆的正文即控制 embedding，反复试探即可命中 B 的最近邻；命中之后每一条下游都越过归属：`_dedup_structural` 把 **B 的正文 + A 的正文一起送进外部 LLM**（内容离开本机即无法撤回）、`_dedup_merge` 改 B 的 `importance`（考功与遗忘的输入）、`_create_update_proposal` 把提案的 `target_memory_id` 钉在 B 的记忆上、`find_similar` 把 B 的 ORM 对象直接交给写者、`decide` 拿 B 的记忆判 A 的候选是否冲突并**降 B 的 importance**、往 B 的记忆上写 `ConflictEvent`。B 全程看不到任何回显。
  - **票面原计划「照抄范本 `hybrid.py:432-439`」——先实证了一遍，范本本身是坏的**，两处既存失效：① **Chroma 的 `where` 只接受一个顶层算符**，多键平铺直接 `ValueError`，原代码在 `lanes` 与 principal 条件都出现时拼多键 → 整次查询抛错 → 被上层 `except` 吞掉 → **静默降级成纯关键词检索，向量召回整条通道消失**；② **plain equality 滤 `user_id` 会丢 NULL 属主**：探真实库 353 条向量里 **332 条 `user_id == ''`**、0 条具体用户名，657 行 memoryitem 里 636 行 `user_id IS NULL`，admin principal 因此拿到 0 条向量结果。本票把范本修掉再复用。
  - **新助手 `core.acl.vector_owner_filter(principal, extra=None)` 做单一真源**：`admin` / `principal=None` → 不过滤（worker/CLI 不能空转）；否则 `$or [user_id=viewer, user_id=""]`——**空串是 NULL 属主在向量库里的落点**（写入侧 `getattr(mem, "user_id", "") or ""` 的 8 键 metadata 契约，已实证）。NULL 属主不等于「属于所有人」，但判不可见会让单人部署整体空转，所以是 `OR ""` 而不是 `== viewer`。多个条件一律塞 `$and`（Chroma 限制）。
  - **`find_similar` 的 `session.get` 盲区单独补判定**：向量层滤对了，按主键直读的那一下仍绕开 SQL scope（票 14 踩过两次）。取到行后过 `_owns`。
  - **principal 一路下传**：`add_memory` / `add_memory_async` 把已有的 `user_id` / `tenant_id` 经 `_principal_of` 构造成 principal，传到 `_apply_dedup` / `_dedup_structural` / `_dedup_merge` / `_create_update_proposal` / `_create_candidate_direct` / `_create_candidate_with_extraction` / `find_similar` / `decide`。两条路径（fastpath 直书与提取）都要走——漏一个就是半修。
  - **探针抓到一处票面外越权**：`decide` 在 `principal=None` 时会自行按候选自身属主收敛，所以路由丢掉 `ctx` 后 B 的记忆一行不动、原有断言看不见；但 **B 的裁决结果仍由 A 说了算**——A 拿 B 的 `candidate_id` 打 `/gate`，能让 B 的待决内容晋升成正式记忆。已按票 02 的 `evolution_service._ensure_can_decide` 范式补 `_ensure_can_decide`（复用 `acl.ensure_can_delete` 单一真源，含租户维度）。
  - **测试增量**：`tests/test_dedup_ownership.py` 42 例。决定性断言落在三处真出口上——LLM 提示词文本、落库行字段值、HTTP 状态码。含多条反向用例保功能没被修废（A 自己的重复内容照常 merge bump、NULL 属主老行照常参与比对、admin 与 `principal=None` 全表）。`_apply_dedup` 与 `hybrid_search` 的「filters 真的传下去了」用**真 Chroma**（临时目录）验，不 mock 检索本身——mock 掉的 store 收不收 filters 都行，断言会退化成「调用签名里有这个 kwarg」。
  - **顺带修掉两个测试自身的假绿机器**：`test_v022_retrieval.py` 的向量替身 `_respect` 只认平铺键，遇到 `$and` 键 `getattr(row, "$and")` → None → **所有行被滤掉**，「A 检索不到 B 的记忆」退化成「A 什么都检索不到」——绿得毫无意义（已升级为真 Chroma 语义 + `user_id` 走向量库口径 NULL→空串）；`test_coalesce_async.py` 两处精确匹配断言补 `principal` kwarg。
  - **变异验证 27/27 全杀**（`.scratch/readside-gaps/mutation_check_15.py`，子进程隔离）。第一轮 **11 个 MISSED** 全部对应真实缺口——`_owns` 守卫全被杀，缺的是**下传链**与 `decide` / 路由层（直接用 service 的测试够不着 `add_memory` 内部的管线）；补测试后剩 1 个，探针查出是上面那处票面外越权，补 `_ensure_can_decide` + M26/M27 后全杀。全量 pytest **1668 passed / 0 failed**（基线 1626）。
  - **等价变异不是覆盖缺口**：M27 最初设计成「裁决校验跳过 admin」，但 `ensure_can_delete` 本就对 admin 早退——该变异与原程序语义等价，任何测试都不可能区分。换成真正会改行为的「丢租户维度」并补跨租户用例。

- **分类树读侧归属——`/tree` 不再把整棵树的节点描述和别人的挂载条数一起吐出来（2026-09-28，票据 `.scratch/readside-gaps/issues/08-tree-edges-readside-no-identity.md`）**：
  - **先说影响**：`GET /tree` 和 `/tree/subtree` 一个身份都不取，返回**整棵树**的节点。漏的不只是节点名——每个节点带一段 `description` 自由文本（第五轮实证 A 打过去 len=189，含 B 的密文），更隐蔽的是**挂载计数**：节点名就算收窄了，计数照样漏，**A 能数出 B 在某个节点下挂了多少条记忆**。两处分开漏，任漏一处都够推出「B 在忙什么」。
  - **`MemoryNode` 此前一个归属列都没有** → 补 `tenant_id` / `user_id` / `agent_id` + 迁移 v26（幂等 `_has_column` 守卫，异常只记日志不阻断启动，同 `SessionCheckpoint` 口径）。真实库 11 行老数据保持 NULL，读侧靠 `OR IS NULL` 兜住——NULL 是「未记录」不是「属于所有人」，判不可见会让整棵树在单人部署下直接消失。
  - **两处同批收窄**：节点查询和挂载计数查询各配一个 scope（`_node_scope` / `_memory_scope`），口径与票 03/04/06/09 逐字一致（admin / `principal=None` → 不过滤；否则 `user_id == viewer OR IS NULL`）。`session_id` 不加——节点是跨会话共享的分类结构，按 session 归属会让每次新会话都看不到自己建的节点。
  - **写侧**：`add_node` 新建节点落 `principal` 的三个归属列；`principal=None`（内部/脚本）留 NULL。三个 handler（`/tree`、`/tree/nodes`、`/tree/subtree`）补 `Depends(get_current_user)` 并下传——直接调 service 只证明 service 修好了，宿主打的是 HTTP，路由少取一次身份泄漏照样发生（票 12/13 都在路由层栽过）。
  - **测试增量**：`tests/test_tree_ownership.py` 14 例。Red 1 的断言落在 `node_path` 与 `description` 上而不只是「机密不在响应里」——后者会因为节点本来就叫别的名字而空过。含三条反向用例保功能没被修废（NULL 属主老节点照常可见、A 自己的挂载照常计数、owner 对自己的记忆照旧能挂），admin 与 `principal=None` 两条全表用例，路由层三条 HTTP 出口复现，迁移幂等一条。
  - **变异验证 16/16 全杀**（`.scratch/readside-gaps/mutation_check_08.py`，子进程隔离）：scope 本体五条（恒不收窄 / 丢 `OR IS NULL` / 计数不收窄）、节点与计数两条查询不加 scope、形参链五条、路由层六条。全量 pytest **1626 passed / 0 failed**（基线 1612）。
  - **连带修正**：`CURRENT_SCHEMA_VERSION` 升到 26 时漏改常量，24 个迁移测试当场红——升 schema 版本号必须同步改 `lantai/storage/db.py:19` 的常量，否则 `PRAGMA user_version` 链断在最后一环。

- **反思全表扫描——A 触发一次，B 的记忆正文不再被送进外部 LLM 提示词（2026-09-28，票据 `.scratch/readside-gaps/issues/14-reflect-leaks-cross-user.md`）**：
  - **先说影响**：前十三票治的都是「A 从本系统读到 B 的数据」；这一票是**A 能把 B 的数据送到系统外的 LLM**。`health_scan` 三处全表扫描一个身份都不取，`_curate()` 把候选拼进 user prompt 调 `chat_json`——spy 直证 B 的记忆正文原样出现在发给外部 LLM 的提示词里：`<memory_data id="m-B">B 的银行密码是 9527</memory_data>`。**内容离开本机边界，且无法撤回。**
  - **`wrap_as_data` 的围栏只防注入、不防归属**：它假设「拼进提示词的内容本来就是有权看的」。所以围栏照旧，归属另修——这是本票与「提示词安全」最容易混为一谈的地方。
  - **四条扫描路径，同批修**（分开修会留下「以为修完了」的错觉）：候选集（`health_scan`）、related 记忆、theme 触发（水位达标时全表扫新记忆）、**rejecter 的 `evidence_ids`**。前三处是票面预判的；**第四处是探针实测抓到的**——`prop.evidence_ids` 来自 LLM 输出，是**攻击者可控字段**，curator 只要回一个别人的记忆 id，该正文就被 `s.get(MemoryItem, eid)` 按主键直读出来拼进 rejecter 提示词。四处统一过 `_reflect_scope(principal)`，口径与票 12 的 `_consolidation_scope` / `_kaogong_scope` 逐字一致。
  - **`session.get` 是归属盲区，本票踩了两次**：`health_scan` 的 open 冲突账本、rejecter 的 `evidence_ids`，都是按主键直读、绕开 SQL 层 scope。凡是 `session.get(MemoryItem, ...)` 的取值点都要单独补判定。
  - **`principal=None` 保持全表**（scheduler / worker）：反思是系统行为，收窄成空转会让 open 冲突账本与陈旧记忆永远没人处理。**这个口径是刻意的**，已写进 `_reflect_scope` docstring，别让后来人以为漏了。
  - **MCP 不猜身份**：`handle_reflect_run` 复用票 10 的 `_principal_from_params`——宿主透传 `user_id` 才收窄，不透传留 NULL 不过滤。
  - **测试增量**：`tests/test_reflect_ownership.py` 16 例。**决定性断言落在提示词文本上**——只看 candidates 列表等于只看中间量，提示词才是内容真正离开本机的出口。含三条「反向」用例保功能没被修废（A 自己的 superseded 记忆照常进提示词、NULL 属主老行照常进、A 自己的证据照常进 rejecter），以及 admin 与 `principal=None` 两条全表用例。
  - **踩坑：`patch(...) as spy` 让断言整体空转（本票最险的一处）**：`patch("...chat_json", side_effect=PromptSpy()) as spy` 绑到的 `spy` 是 **MagicMock**，于是 `spy.prompts` 也是 MagicMock、`spy.prompts[0]` 还是 MagicMock，而 `SECRET in <MagicMock>` 恒为 False——`not any(...)` 恒真，测试一片绿且**看不出任何异常**。theme 路径那条就是这样骗过第一轮变异验证的（3 个 MISSED）。修法：spy 必须是独立变量，`patch(..., side_effect=spy)` 不写 `as`。**同源坑**：断言只查「4xx 满足」而 422 也算 4xx（票 13 已踩）。
  - **探针第一轮也抓到同一处**：第九轮探针最初只种 B 的记忆，收窄正确时 A 的候选集为空、反思直接 idle、**LLM 一次都不调**——「没进提示词」是因为什么都没跑。补种 A 自己的候选后才暴露出 rejecter 那条真泄漏。**「没观察到」和「没发生」必须分开证**，两边都栽了同一个跟头。
  - **变异验证 13/13 全杀**（`.scratch/readside-gaps/mutation_check_14.py`，子进程隔离）：四条扫描路径各一条、scope 本体三条（恒不收窄 / 忽略 admin / 丢 `OR IS NULL`）、形参链四条、MCP 一条。第一轮 **3 个 MISSED** 全部源于上述 spy 空转，补好后全杀。全量 pytest **1612 passed / 0 failed**（基线 1596）。
  - **探针复验**：`probe_round9.py` 三条判据全 ok（evolve 不改写 B、候选集不含 B、LLM 提示词不含 B 的正文），且 LLM 路径**真的跑起来了**（1 次 curator 调用；rejecter 因证据被正确收窄而按「无证据」判 high，不再调 LLM——这正是宁 miss 不脏写）。
  - **待实证**：`lantai/cognition/reflection.py` 的 `ReflectionEngine`（`POST /cognitive/reflect`）是**另一套**反思引擎，作用于 Observation/Pattern/Belief 表、不碰 `MemoryItem` 正文、不调 LLM 送内容，本票未覆盖。

- **定点写入口归属——`rollback` 与 `feedback` 不再跨用户改写单条记忆（2026-09-28，票据 `.scratch/readside-gaps/issues/13-rollback-feedback-cross-user.md`）**：
  - **背景实证**（`.scratch/readside-audit/probe_round8.py`）：票 12 修完三个「全库演化」入口后，本轮改查**按 id 定点**的写入口——它们不扫全表，票 12 的候选集收窄对它们完全无效。两个中：`POST /memory/{id}/rollback` 让 A 把 B 的正文**整条覆盖成历史任意版本**（`prev.after` 逐字段 `setattr`，且没有 undo 入口）；`POST /feedback` 让 A 刷 B 的 `use_count`/`helpful_count`/`importance`。判据四条（A 改 B / A 改自己 / admin 改任意 / 行为不变）在两端点上正确区分——**探针不是空转的**。
  - **feedback 为什么也算严重**：那三个字段正是**考功与遗忘的输入**（票 12 刚修的两处即按它们决策）。刷它们等于间接操控别人的演化结果——不是直接写正文，但效果等价且更难察觉。
  - **三条入口同一处代码**，只修一处会留下「以为修完了」的错觉：REST 路由、MCP 工具、service 层（还被 worker/eval 消费）一并修。`promoter.rollback` / `reflector.record_feedback` 加 `principal=None`，取到行后过 `acl.ensure_can_delete` 单一真源（同票 04/06/11 写侧范式），传 `resource_user_id` / `resource_tenant_id` / `lane` 三项（与 `routes_terminal.py:252` 同口径）。
  - **`ok: False` 而非抛异常**：service 契约是 dict（被 worker/eval/MCP 多处消费，形状不能动），403 语义只在路由边界翻译。**`_ok_or_raise` 因此新增 `forbidden` → 403 分支**——原本会落进 422，而 422 是「请求格式有问题」，会让调用方以为自己的 body 写错了，实际是权限不足。
  - **MCP 不猜身份**：两个 handler 复用票 10 的 `_principal_from_params`——宿主透传 `user_id` 才校验，不透传留 NULL 不过滤。不看环境变量/进程名/session_id 推导。
  - **测试增量**：`tests/test_pointwrite_ownership.py` 17 例。除七条 Red 外另加**跨租户**与**泳道越权**两例——`ensure_can_delete` 的这两个分支在单用户部署下不触发，必须显式造一个不同 tenant / 不含该 lane 的主体，否则分支被遮住、变异杀不掉。每条决定性断言落在**落库行字段值**上（`content` / `use_count` / `importance`），不只看返回的 `ok`。
  - **回归修补**：`tests/test_mcp.py::test_rollback_ok` 原本断言 `assert_called_once_with("mem_1")`，MCP 现在多传 `principal=None`。已改为显式钉住这个 kwarg——**顺带让「漏传 identity」在这条既有测试上现形**。
  - **探针复验**：两个 LEAK 全部转 ok，返回 `403 forbidden: resource belongs to another user`；「A 改自己」「admin 改任意」两条仍 ok。
  - **踩坑（与票 12 重复，第二次踩）**：探针的 `create_all` 建不出 FTS5 虚表，`rollback` 路径 `sync_fts` 缺表整笔回滚，须显式 `init_fts`。另：只 patch `db_module.engine` 不够——`get_session` 内部绑模块级 `engine` 名字，必须整体替换 `db_module.get_session`。
  - **变异验证 13/13 全杀**（`.scratch/readside-gaps/mutation_check_13.py`，子进程隔离）。第一轮 **4 个 MISSED** 全对应真实缺口（跨租户分支、泳道分支、403 翻译、MCP feedback），补测试后全杀。全量 pytest **1596 passed / 0 failed**（基线 1579）。
  - **待实证**：`POST /evolve/run` 本轮只覆盖「没有可提案的输入」一种情况，proposer/reflector 都是全表扫描，**不等于安全**；`MemoryUsageFeedback` 只有 `session_id`，`FailureRecord` / `ActionOutcome` 零归属列。

- **演化类写侧归属——考功/沉潜/遗忘不再批量改写别人的记忆（2026-09-28，票据 `.scratch/readside-gaps/issues/12-kaogong-writes-cross-user.md`）**：
  - **背景实证**（`.scratch/readside-audit/probe_round7.py` 实证 + 落库字段核验）：三个「全库演化」入口一个身份都不取，候选集是全表 `select(MemoryItem).where(status=="active")`。考功把 B 的 `importance` 从 **0.9 改写成 0.1**、`tier` 从 `working` 改成 `longterm`、`decay_class` 一并改；沉潜把别人的碎片标成 `consolidated` 并**新生成一条带别人正文的主记忆**；遗忘改 `decay_score` 并把低衰减记忆置 `archived`。**读侧缺口只是「看到」，这里是真改，而且多数不可逆**——`importance` 降了没有回滚路径，`archived` 没有 undo 入口。这正是「宁 miss 不脏写」要防的：宁可漏一次考功，不能错改别人的权重。
  - **三处同构一并修**（分开修会留下「以为修完了」的错觉）：`run_kaogong_cycle` / `find_consolidation_clusters` + `prune_decayed_synapses` / `apply_forgetting` 全部新增 `principal=None` 形参，各配一个 `_*_scope(principal)` 助手；`run_consolidation_cycle` 把 principal 分别透传给聚类与裁剪两条候选集。口径与票 03/04/06/09/10 逐字一致：**admin / `principal=None` → 不过滤**，否则 `user_id == viewer OR IS NULL`。
  - **NULL 属主必须可见**：真实库 615 行 `memoryitem` 是 `user_id IS NULL`（迁移前/脚本直插）。判「不可见」会让单人部署下的考功晋升、衰减归档、碎片折叠整体空转。NULL 是「未记录」不是「属于所有人」。
  - **`principal=None` 保持全表**（worker/CLI/scheduler/定时任务）：演化是系统行为，收窄成空转会让 archive 门槛永不触发、晋升停摆。这个口径是刻意的，票据口径 4 有记录。
  - **路由层**：`POST /evolution/kaogong` 与 `POST /evolution/consolidate` 补 `ctx=Depends(get_current_user)` 并下传。此前这两个端点连身份都不取——任何持 key 者都能借 REST 触发别人的权重改写。
  - **测试增量**：`tests/test_evolution_write_ownership.py` 18 例，三个 service 各一组 + 路由层一组。每条决定性断言都落在**落库行的字段值**上（`importance` / `status` / `decay_score` / `tier`），不只看报告计数——计数为 0 可能有一堆别的原因（没数据、样本不足、状态不对）。含三条「反向」用例保功能没被修废：A 自己的记忆照常降权/晋升/归档、NULL 属主老行照常处理、admin 与 `principal=None` 仍全表。**不 mock 内部逻辑**：jieba 聚类、TrustMem 校验、衰减公式全部真实执行，只替 LLM 提纯段与外部向量存储。
  - **踩坑记录（两条都会让测试变空转，已写进代码注释）**：① `patch("lantai.retrieval.hybrid.index_memory_item")` 不生效——`consolidation_service` 是 `from lantai.retrieval.hybrid import index_memory_item` 的模块级绑定，必须 patch `lantai.services.consolidation_service.index_memory_item`；单跑时 Chroma 单例维度是 8 所以静默通过，**进了全量套件才暴露**（全量先跑者把单例建成 1024 维 → `InvalidDimensionException`）。② 不替 `chat_json` 时真实 LLM 失败 → `consolidate_cluster` 返回 None → 什么都不折叠 → 身份传没传都一片绿。另：`ew_env` fixture 须自建 FTS5 虚表（`create_all` 不建它，少了它整笔折叠回滚）。
  - **变异验证 13/13 全杀**（`.scratch/readside-gaps/mutation_check_12.py`，子进程隔离）：含「候选集不收窄」「作用域忽略 admin」「NULL 判不可见」「形参丢失」「路由不取身份」「周期不透传 principal 给聚类/裁剪」六类。第一轮 5 个 MISSED 全部对应上述真实缺口（NULL 口径、聚类路径、周期透传、路由层、admin 口径），补测试后全杀。全量 pytest **1579 passed / 0 failed**（基线 1577）。

- **读侧归属收窄——`/sources` 来源凭证与 `/retrieval/recent-events` 查询词不再跨用户可读；顺带修掉票 11 归属列迁移在真实库从未生效的真 bug（2026-09-28，票据 `.scratch/readside-gaps/issues/10-sources-recent-events-no-identity.md`）**：
  - **背景实证**（`.scratch/readside-audit/probe_round5.py`）：两个端点一个身份都不取。`GET /sources` 把 `Source.config` 原样吐出——`config` 是 JSON 列，按设计放的就是 http header、token、api key 这类**连接凭证**；`GET /retrieval/recent-events` 吐出 `query_text`——**用户问过什么，比记忆正文更直接暴露意图**，真实库 918 行。探针种一个中一个。
  - **顺带挖出的真 bug（比本票本身更严重）**：票 11 那次归属列迁移**在真实库上从未生效过**。`migrations_v022.py` 写的表名是 `prompt_template` / `skill_crystal`（带下划线），而真实表叫 `prompttemplate` / `skillcrystal`（SQLModel 默认无下划线拼接）。`_has_column` 对**不存在的表返回 True**，所以写错表名**不报错**，只是那次 `ALTER TABLE` 静默不执行——归属列一个都没加，`/prompts`、`/crystals` 的读侧一查不存在的列就是 500。已修表名，并在**真实库副本**上实证：迁移前四表 `user_id=False`，迁移后全部 True，跑两遍不重复加列，919 行数据不动。
  - **实现**：`Source` / `RetrievalEvent` 补归属四元组 + 幂等加列迁移（沿用 `apply_v022_migrations`，不新造第二条迁移链，只加列不回填）；读侧 `_source_scope` / `_event_scope` 按 `user_id == viewer OR IS NULL` 收窄（admin 全权；NULL 是「未记录」不是「属于所有人」，单人部署下判不可见会让功能直接消失）；写侧 `add_source` / `log_retrieval` 落 `principal.user_id`。
  - **两道独立防线**：归属收窄之外，`GET /sources` 另加 `redact_config()`——已知敏感键（token/secret/password/api_key/authorization/cookie 等，大小写不敏感含子串，嵌套 dict 递归）替换为 `***`。**即使归属修好、即使请求方是 admin，也不该把凭证明文回显。** 宁 miss 不脏写：不认识的键原样返回，不做猜测式脱敏（猜错会把正常配置也抹掉，比泄漏更难排查）。
  - **归属身份顺着调用链取，不另取**：REST 侧两个 `_try_log` 调用点透传 `ctx`；MCP 侧无 HTTP 鉴权层，新增 `_principal_from_params()`——宿主透传 `user_id` 才构造 principal，**不透传留 NULL**。不猜身份（不看环境变量、进程名、`session_id` 推导）——猜错等于把别人的查询词挂到另一个人头上，比 NULL 更难排查。
  - **测试增量**：`tests/test_sources_recent_events_ownership.py` 14 例 + `tests/test_ownership_migration.py` 扩充至 11 例，均含不 mock 冒烟（真打 FastAPI TestClient、真调 `log_retrieval`、真实 SQLite 文件库跑迁移）。**变异验证 18/18 全杀**（子进程隔离，含「迁移表名退回带下划线」M18 专治票 11 事故重演、「REST/MCP 各两个埋点调用点漏传 principal」M7/M8/M17）。全量 pytest **1561 passed / 0 failed**（基线 1543）。
  - **探针复验**：`.scratch/readside-audit/probe_round6.py` 显式给种子落 `user_id`（第五轮种子是 NULL 属主，读侧放行本是正确行为，那次 ok 什么都没证明），判据从「有没有泄漏」扩成四条：A 看不到 B / A 看得到自己 / NULL 老行仍可见 / admin 见得到全部。**并反向验证过探针不是空转**——把读侧收窄改坏，探针立刻报 LEAK 退出码 1。

### Fixed

- **外部 LLM 调用统一替身——162 次真实联网归零，全量测试快 23 倍（2026-09-28，票据 `.scratch/test-env-parity/issues/01-test-isolation-env-dependence.md`，维护者选定方案甲）**：
  - **背景**：spy 实证（包住 `lantai.llm.client` 真实实现记录产品代码栈帧，跑全量）发现 **11 个产品代码调用点、162 次真实外部调用**——`retrieval/intent.py:24`(67)、`services/memory_service.py:51`(21)、`evolution/proposer.py:26`(14)、`parsing/extractor.py:9`(12)、`gate/decision.py:24`(9)、`services/auto_triage_service.py:62`(4)、`gate/contradiction.py:9`(3)、`services/import_service.py:126`(2)、`retrieval/hybrid.py:431`(2)、`services/record_ops_service.py:101`(1)。本地 `.env` 有真 key 所以走主路径，CI 用 conftest 假 key 拿到 `AuthenticationError` 被 `except` 吞掉走降级路径——**两个环境跑的不是同一条路**，只是目前两条路产出同样断言结果（巧合，非保证）。
  - **实现**（`tests/conftest.py`）：autouse fixture `_stub_external_llm` + `_LLM_MODULE_BINDINGS` 清单（19 个模块级 import 点）。**只 patch 源模块不够**——`from lantai.llm.client import embed` 会在各模块各存一份绑定，必须连模块级 import 点一起替；**函数内 import 自动跟随源 patch**（实测验证，那 8 处无需列举）。另处理两个 `from lantai.llm import client as llm_client` 形态（属性名是 `llm_client` 而非 `embed`）。
  - **替身取值**：`embed` 返回 **1024 维**（与真实 `EMBED_MODEL=bge-m3` 一致——维度不符会让调用点被提前踢进 `except`，替身就失去意义）；`chat_json` 返回**空 dict**（刻意不喂业务字段，避免把「LLM 恰好返回什么」变成隐式契约；需要特定返回值的测试自行 patch，晚于本 fixture 生效、撕卸自动还原——既有范式）。
  - **实证成果**：逃逸次数 **162 → 0**；全量 **1285 passed / 0 failed**；**耗时 2190s → 101s（快 23 倍）**——此前全量大半时间耗在真连 API 上。
  - **测试增量**：`tests/test_env_isolation.py` 增 5 例。判据用**行为特征**（调用它看返回是否为替身特征值）而非 `is 某函数对象`——因为 `tests/conftest.py` 会被 import 两次（pytest rootdir 加载 + 测试内 `from tests.conftest import ...`），两个模块对象的同名函数不是同一个（首轮实测踩到）。**变异验证**：fixture 不 patch 模块级绑定 → 逃逸被抓住、测试 failed；恢复后全绿。

- **测试数据目录隔离——`db.engine` 不再绑定宿主真实库（2026-09-27，票据 `.scratch/test-env-parity/issues/02-test-data-dir-isolation.md`）**：
  - **背景**：本地 `.env` 的 `LANTAI_HOME` 指向真实开发库（`C:\Users\Asus\AppData\Local\remembrance-data`），而 `lantai/storage/db.py:13` 的 `engine` 在 **import 时**就按 `settings.DATABASE_URL` 建好——测试进程因此绑定宿主真实 SQLite/ChromaDB。CI 是干净目录、本地不是，这个差异正是「本地绿 CI 红」根因家族（`.scratch/test-env-parity/` spec 的动机表列了三项，本票治第三项）。
  - **实现**（`tests/conftest.py`）：新增 session 级 autouse fixture `_isolate_data_dir`——`settings.LANTAI_HOME` / `DATABASE_URL` / `CHROMADB_PATH` 重指到 `tmp_path_factory` 临时目录，并把 `db_module.engine` 重指到临时库。**只改 settings 无效**（engine 已于 import 时建好），这是本票的核心动作；`get_session()` 引用模块级 `engine` 名字（晚期绑定）故自动跟随。
  - **坑一**：SQLite **不会自动建父目录**，`LANTAI_HOME` 指向不存在的目录会 `unable to open database file`（实测 45 例 setup 连坐）→ fixture 内先 `mkdir` + `touch` 库文件。
  - **坑二**：与既有绊线（`pytest_runtest_setup` 校验 `db.engine` 身份）冲突——隔离后每个测试都会被误报「前置测试污染了 db.engine」。故 fixture 内把绊线基线 `_ORIG_ENGINE` 更新为隔离后的 engine（隔离后的即「合法身份」）。**这两行必须成对出现**，只改其一会让全部测试误报（变异验证实测 10 例连坐），已在代码里标注警示。
  - **测试增量**：`tests/test_env_isolation.py` 4 例不 mock 冒烟，直读测试进程内的 engine/settings 状态，并真往隔离库写一行再查回（隔离是否生效是可观测事实，mock 掉就什么都验证不了）。**变异验证**：去掉 `db_module.engine = engine` 重定向 → 4 例全红（1 failed + 3 error）；恢复后全绿。
  - **验收**：全量 pytest **1277 passed / 0 failed**（开工基线 1276 passed 1 failed——那个偶发顺序失败随隔离消失）。
  - **方法论修正（重要，记录以免重蹈）**：最初据「真实 `.chromadb` 目录 mtime 落在跑测窗口内」判定「测试在写真实库」，**该结论错误**。后续对照实验发现**不跑测试时真实库 mtime 也每秒在变**——本机有 4 个 `E:/Hermes/scripts/lantai_mcp_bridge.py` 后台进程在持续写同一个真实库。改用**内容指纹**复验：跑 `test_dedup_flow` + `test_provenance` + `test_scene` 前后，真实库 `memoryitem` 行数 **593 → 593 不变**，即测试并未写真实 SQLite。**教训：判定「测试是否写了某资源」必须先做「不跑测试」的同期对照，排除后台进程噪声。** 隔离 fixture 的价值不变且更强——它消除的是「测试依赖宿主环境状态」这个结构性问题，以及后台进程某天写出会让测试行为分叉的数据的可能。

- **CI 测试门禁 lint 收口——50 条存量违规清零，全量 pytest 第一次真正在 CI 上跑（2026-09-26，票据 `.scratch/ci-lint-gate/issues/01-ci-lint-gate.md`）**：
  - **背景实证**：v0.22.1 push 后 Tests job 51 秒挂在 Ruff lint 步骤（仓库带 73 条既有违规，多在 `docs/research` 与 `.scratch` 的一次性调研脚本里），**全量 pytest 与遗忘质量门禁从未在 CI 执行过**——「CI 绿」从未真正验证过测试。本地 Windows/Py3.13 全量通过是唯一防线，CI 的 Linux/Py3.11 平台差异从未被门禁覆盖。
  - **门禁范围收口**（`.github/workflows/tests.yml`）：`ruff check .` / `ruff format --check .` → `ruff check lantai/ tests/ scripts/` / `ruff format --check lantai/ tests/ scripts/`。目录显式列出而非依赖 `exclude` 配置——显式传径时 ruff 的 exclude 不生效（实测 `[tool.ruff.format] exclude` 配 `docs` 后仍报 5 个 markdown）。`[tool.ruff] extend-exclude` 保留（对裸 `ruff check .` 生效，本地开发同样绿）。
  - **排除的非产品代码**：`docs/` 下 markdown 内嵌 python 代码块（格式化会改**文档正文**，5 个 ADR/plan/spec）、`docs/research` 三个一次性图表脚本（23 条违规）、`.scratch` 三个草稿脚本（7 条违规）。若要把它们纳入，须先清理存量并单独成一票。
  - **50 条存量违规全清**（39 自动 + 11 手工）：`UP017`×18（`timezone.utc` → `UTC`，同一 `datetime.timezone` 单例）、`I001`×16（import 排序）、`F841`×6（删未用赋值，构造/调用副作用保留）、`SIM105`×2（`try/except/pass` → `contextlib.suppress`）、`SIM300`×2、`F811`×1（删遮蔽模块级的函数内 `datetime` 导入）、`B010`×1（`setattr` → 属性赋值）、`E731`×1（`lambda` → `def`）、`UP031`×1（%-格式化 → f-string，4 个输入逐字节一致）、`SIM103`×1（`_validity_hit` 两条 `return False` 合并为单一 `return not(...)`，25 组输入真值表全等）、`F632`×1。
  - **顺带修掉一处恒真的漏检断言**（`tests/test_staged_eval.py`）：原句第三项 `"failure_buckets" is not None` 比对的是**字符串字面量**而非 `st["failure_buckets"]` 的值（恒真，Python 甚至发 SyntaxWarning），根本没检查该键的值——改为 `assert st["failure_buckets"] is not None` 恢复本意。这类 bug 在会跑的 CI 上活不下来，正是 lint 挡门禁欠的债。
  - **自动修避坑**：`ruff --fix` 会把 `UTC = timezone.utc` 生成无意义的 `UTC = UTC` 自赋值（3 文件），手工改为 `from datetime import UTC` 并删别名行，全仓 0 处。
  - **验收**：`ruff check` / `ruff format --check` 退出码 0（修复前 50 条违规 + 50 文件不合规）；全量 pytest **1249 passed / 0 failed**（与 v0.22.1 基线同数，零回归）；遗忘质量门禁 **6/6 PASS**。
  - **待实证**：push 后 Tests job 应第一次真正跑完 pytest 与遗忘门禁。Linux/Py3.11 平台差异可能暴露新的既有失败（本地是 Windows/Py3.13）——若出现，按「宁 miss 不脏写」逐条诊断，不静默跳过。

### Fixed

- **静默失败清零系列 第二批——三处「坏了看不出来」改成「坏了看得见」（2026-09-28，票据 `.scratch/proposal-livelock/`、`.scratch/fts-availability/`、`.scratch/supersedes-silent-failure/`）**：
  - **⑥ event_time 违例早退导致 APPROVED livelock**（`evolution/promoter.py`，票据 `proposal-livelock`）：`apply_proposal` 里 I1 校验失败时 `return out` 早退，但**没改提案状态**——提案停在 `APPROVED`，而 `evolve_worker.run_pending_proposals()` 专挑 `status == APPROVED` 的活，于是同一个坏提案被反复捞起、反复早退，永不收敛。修法：走既有的 `_reject_proposal` 助手落终态 `REJECTED`（与同函数 :275 的陈旧闸门同口径），日志留痕。**这是真 livelock 而非仅终局陷阱**——worker 会一直空转。
  - **⑦ `init_fts` 静默失败，FTS5 坏了 `/health` 还说 OK**（`storage/fts.py` + `storage/db.py` + `api/routes_health.py`，票据 `fts-availability`）：`CREATE VIRTUAL TABLE IF NOT EXISTS` 在目标已存在时是**静默 no-op**——SQLite 不报错，哪怕已存在的是张普通表、或分词器不对的 FTS5 表。实测两种坏态：同名普通表 → 之后每次查询 `no such column: memory_fts`，词汇召回整体消失；FTS5 但 `tokenize≠trigram` → bm25 查询照样成功，只是**中文子串召回永久失效且任何一层都不报错**。修法：`init_fts` 返回 `bool`，CREATE 之后**回读 `sqlite_master` 的真实 DDL** 核验 `fts5`/`trigram` 两个关键字都在才敢声称成功；`db.py` 置模块级 `FTS_OK`（None = 尚未初始化，与「查了是坏的」区分）；`/health/deep` 增 `checks["fts"]`。**不让它抛异常**——抛出去会连坐整个服务启动，而这本是「降级但可用」。**不自动 DROP 重建**——可能误删别人的表（宁 miss 不脏写）。
  - **⑧ supersedes 边查询失败静默 → 陈旧值排在更正值之上**（`retrieval/hybrid.py`，票据 `supersedes-silent-failure`）：`_apply_supersedes_order` 的 except 原样返回未降权排序。这不是普通 miss，是**方向性错误**——旧值 0.90 / 新更正 0.88 的场景下（RRF + 衰减噪声下更正常略低），用户看到的是**已撤回的事实**。降权 epsilon=1e-6 刻意薄到刚好够打破这种毫厘之差，静默 except 抹掉的正是整个机制唯一的作用场景。修法：`logger.warning` + explain 里逐条标 `supersedes_unavailable: True`。**只加留痕，不改降级行为**（仍 `return scored`）——三处调用点都在昂贵工作之后，在此抛异常会把每次 DB 抖动变成 `POST /search` 硬 500，与模块既有的三级降级设计矛盾。
  - **测试增量**：`tests/test_consolidation.py` +4、`tests/test_fts_integration.py` +8、`tests/test_fts_integration.py` +4。每条均做**变异验证**（`.scratch/*/mutation_check.py`）。
  - **验收**：全量 pytest **1373 passed / 0 failed**（第二批开工基线 1369，零回归）。

- **静默失败清零系列——五处「坏了看不出来」改成「坏了看得见」（2026-09-28，票据 `.scratch/migration-observability/`、`.scratch/retrieval-silent-failure/`、`.scratch/gate-fail-open/`、`.scratch/proposal-type-whitelist/`、`.scratch/api-error-status/`）**：
  - **共同病根**：`except Exception: pass` 把「子系统坏了」渲染成与「子系统正常但没查到东西」完全同形的返回值。用户侧表现为「结果变少了」——无法区分是内容不相关还是索引/通道坏了。这类 bug 的共同点是**没有任何日志痕迹**，连排查入口都没有。
  - **① 迁移期四处索引创建失败**（`storage/db.py`，票据 `migration-observability`）：v17/v20/v21/v22 四个迁移点的 `CREATE INDEX` 失败被 `pass` 吞掉。后果：库长期缺索引，检索/去重查询全表扫，越用越慢且无人知晓。修法：`logger.warning` 留痕（含索引名与原因）。**顺带实证一个认知坑**：v17 迁移先 `ALTER TABLE ADD COLUMN domain` 再建索引，所以「缺列的旧库」仍能成功建索引——最初据此写的测试（缺列库应告警）是错的前提，改用**索引名撞表名**才真触发失败。
  - **② 检索三处降级通道静默**（`retrieval/hybrid.py`，票据 `retrieval-silent-failure`）：BM25 通道、向量失败后的 FTS-hit 通道、最后的 LIKE 兜底三处 `except: pass`。三级兜底全静默的后果：检索能力已耗尽、即将返回空，而日志一片安静。修法：三处 `logger.warning`，口径与同文件既有的向量失败留痕对齐。
  - **③ 矛盾检测失败静默放行**（`gate/contradiction.py` + `gate/decision.py`，票据 `gate-fail-open`）：`check_contradiction` 的 except 返回 `{"contradicts": False}`，与「检测器说没矛盾」同形；`decide()` site 1 再包一层 try/except，双保险静默。**LLM 一挂（超时/429/5xx/key 失效），矛盾检测整条通道失效且零日志，候选长驱直入入库**——这是本轮最严重的一处。修法：失败返回加附加键 `check_unavailable: True`（成功路径返回形状一字未改），`decide()` 据此判 `REJECT`，由 `evolve_worker` 把候选推进**待审队列**交人裁决。**不新加 `GateDecision` 成员**——`staged.py` 的 scorer 对未知成员会静默错算、`evolve_worker.py:23` 的 `== "reject"` 分支也会漏接，用既有 REJECT + 可区分 reason 是唯一不引入新故障面的形状。ADR-0024（site 2，否定对探测）语义**不动**——该处本就是探测型输入、ADR 明载宁 miss。
  - **④ 提案类型白名单闸**（`evolution/promoter.py` + `evolution/proposer.py`，票据 `proposal-type-whitelist`）：`apply_proposal` 末行兜底 `elif proposal_type == "add" or not existing:` 会吞掉任何未知类型——反射器若产出 `split`/`link` 等类型，结果不是报错而是**新建一条平行记忆** + trigger 记 gate + 多出 supports 边与 Evidence，而提案本想做的事一件没做（脏写且不可逆）。修法：显式枚举 `VALID_PROPOSAL_TYPES`（含 consolidation——它由 consolidation_service 直写产生，与 reflector 的产出面不同），不认识的类型显式 REJECTED + 留痕，**不降级为 add**。
  - **⑤ 演化路由把 service 错误返回成 HTTP 200**（`api/routes_evolution.py`，票据 `api-error-status`）：`decide` / `rollback` / `feedback` 三个路由对 service 的 `{"ok": False, "reason": ...}` 原样 200 返回——HTTP 语义上「成功」、实际失败，调用方必须翻 body 才知道出错。修法：路由边界加 `_ok_or_raise()`，把 `ok: False` 翻成 404/409/422，**service 返回形状零变更**（该契约被 worker/eval/MCP 多处消费）。
  - **顺带抓出一个被掩盖的真 bug**：`tests/test_e2e.py::TestFeedback::test_feedback` 原来拿 `/add` 返回的 `candidate_id` 当 `memory_id` 传给 `/feedback`——两者 id 空间不同，**反馈从未落库**，但旧代码返回 `{"ok": False}` 而路由原样 200，测试就一直「绿着」通过。状态码改造后这条谎言兜不住了（422），遂改为先建一条真记忆再反馈。这正是「错误不得伪装成成功」该有的效果：**改造 first 抓到的就是被掩盖的真 bug**。
  - **测试增量**：`tests/test_migrations.py` +4、`tests/test_fts_integration.py` +4、`tests/test_conflict_rules.py` +8、`tests/test_proposal_target_gate.py` +15、`tests/test_console_api_contracts.py` +4、`tests/test_e2e.py` +1/-1 改写。全部**不 mock 内部计算逻辑**（mock 仅用于隔离 DB 与外部 LLM）。每条均做**变异验证**（故意破坏修复确认测试变红），避免「改了 4 处只测到 1 处」的覆盖假象——迁移票最初就踩过这个坑。
  - **验收**：全量 pytest **1357 passed / 0 failed**（本轮开工基线 1343，零回归）。

- **sqlmodel 0.0.47 强制时区——naive datetime 写库被拒，CI 全量测试第一次跑即红（2026-09-26，票据 `.scratch/naive-datetime-gate/issues/01-naive-datetime.md`）**：
  - **实证**：上一条 lint 收口 push 后，CI Tests job 第一次跑完全量 pytest 即红 **37 failed / 1212 passed**。本地复现（装 0.0.47）36/37 同样失败——不是平台差异，是**依赖版本漂移**：CI 装 `sqlmodel==0.0.47`（`>=0.0.22` 无上界，pip 取最新），本地是 `0.0.42`。
  - **机制**：0.0.47 起 `sqlmodel/sql/sqltypes.py` 的 `UTCDateTime.process_bind_param` 对 naive datetime **直接 raise `ValueError: Datetime values must have timezone information`**（0.0.42 用的是 SQLAlchemy 原生 `DateTime`，naive 值直接放过）。读侧 `process_result_value` 同时改为对无偏移落盘文本补 `tzinfo=utc` 再返回——**读回值从 naive 变 aware**，继续拿 naive 字面量比会撞 `TypeError: can't compare offset-naive and offset-aware`。
  - **落盘格式不变**（实测：aware 值入库时 `astimezone(UTC)` 去偏移，与 naive 版写出的 SQLite 文本逐字节相同），所以这是低风险迁移——**只补 tzinfo，不动任何时刻数值**。
  - **诊断修正**：naive 来源**产品代码占三处**，不是「全在测试夹具层」——`digest_worker.py`（`_local_day_window_utc` / `collect_calibration_stats` 把窗口边界剥成 naive 后当查询绑定参数）、`ingestion/import_service.py` + `services/import_service.py`（`normalize_timestamp` / `_parse_dt` 显式剥 tzinfo，结果写 `created_at`/`updated_at`）、`core/time_precision.py:extract_explicit_event_time`（返回 naive → `.isoformat()` 落 provenance → promoter / record_ops `fromisoformat` → 写 `event_time`）。
  - **修法**：上述三处产品路径改返回/传递 aware UTC；`evolution/promoter.py` 与 `services/record_ops_service.py` 的 `fromisoformat` 改调新增单一真源 `lantai/core/time.py:parse_iso_utc`（同模块新增 `ensure_aware`）；测试夹具与断言语义同步改 aware（`_utc_naive()` → `_now_aware()`、`ORIGINAL_TS` 加 `tzinfo=UTC`、`tzinfo is None` 断言改 `utcoffset() == timedelta(0)`、naive/aware 比较改 aware 对 aware）。**I1 拒写路径原样保留**（promoter 的 ValueError 早退分支不动）。
  - **依赖锁**：`pyproject.toml` `sqlmodel>=0.0.22` → `>=0.0.22,<0.0.48`。挡住未评估的破坏性行为变更；0.0.48+ 升级另票评测。这一条正是本轮 37 例失败的机制——不锁上界，「本地绿 CI 红」的假象会再造。
  - **验收**：原先 32 例失败全清，逐文件复验通过（`test_digest` 11 / `test_eval_query_set` 10 / `test_genglou_ingest` 8 / `test_import`+`test_import_jsonl` 16 / `test_mem_command` 5 / `test_param_reliability`+`test_param_shadow` 36 / `test_recall_chain` 7 / `test_distill` 15）。CI 里单独失败的 `test_distill.py::test_stored_distill_recallable_via_hybrid_search` 本地 15/15 全过，证实是前序失败污染、非独立缺陷。
  - **双版本复验**（锁上界后两个版本都必须绿）：`sqlmodel==0.0.47`（CI 实际版本）**1249 passed / 0 failed** + 遗忘门禁 6/6；`sqlmodel==0.0.42`（本地原版本）**1263 passed / 0 failed**（多出的 14 例是本轮新增的 `tests/test_core_time.py`）。
  - **读侧口径**（重要）：读回值的 tzinfo 是**版本相关**的——0.0.47 的 `process_result_value` 给无偏移落盘文本补 `tzinfo=utc`（读回 aware），0.0.42 用原生 `DateTime`（读回 naive）。产品代码不受影响（比较全走 `_ensure_utc` 归一或在 SQL 侧比，落盘文本两版逐字节相同）；测试断言因此改为只锁**时刻**、不锁时区标注（`tests/test_import.py` / `tests/test_import_jsonl.py` 的 `assert_same_moment`，`tests/test_param_shadow.py` 的 deadline 先归一再比），否则会 `TypeError: can't compare offset-naive and offset-aware`。
  - **测试增量**：`tests/test_core_time.py` 14 例不 mock 冒烟，直调 `utcnow` / `ensure_aware` / `parse_iso_utc`（aware 归一、naive 按 UTC 解释、None 透传、非法 ISO 抛错、写库守门）。这两个新助手是 datetime 列写库的唯一时区守门员，被 import_service / promoter / record_ops_service 三处产品路径调用。**变异验证**：`parse_iso_utc` 去掉 `ensure_aware` 包装 → naive 字符串用例 failed；`ensure_aware` 删 naive 分支 → 3 例 failed（还原后全绿）。
  - **追加：CI 仍红一例——`_distill` 测试助手漏 patch embedding，真连了网（2026-09-27，run 36257985267）**：
    - push 后 CI 的 **lint 步骤第一次通过**（历史上从未通过过），全量 pytest 也第一次跑完（13 分钟，此前 51 秒就挂在 lint）。但 **1 failed**：`tests/test_distill.py::TestDistill::test_stored_distill_recallable_via_hybrid_search` 断言「精华应经闸门管线落为 distill 泳道记忆」拿到 None。
    - **根因（实证）**：CI 日志 `Captured stdout call` 段有 `dedup prescreen failed (insert fallback): RetryError[...AuthenticationError]`。`_distill` 助手只 patch 了 `distill_service.chat_json`，而 `store=True` 会走 `add_memory` → `_create_candidate_direct` → `_apply_dedup` → `embed(...)`——**真实外部网络调用**。本地有真 key 所以成功；CI 用 `tests/conftest.py` 的假 key `"test-key"`（其注释「不会真调 API」在此路径上不成立）拿到 AuthenticationError，被 catch 后回退 insert，两个环境走不同分支 → **本地绿 CI 红**。
    - **修法**：`_distill` 内补两个 patch——`lantai.llm.client.embed`（源绑定）与 `lantai.services.memory_service.embed`（`from X import embed` 会各存一份，只替源不替已绑定引用不生效）。**实证**：不加 patch 稳定复现 AuthenticationError 警告；加上后警告变为 `Embedding dimension 8 does not match collection dimensionality 1024`（假向量 8 维 vs 真 ChromaDB collection 1024 维——已非网络调用）。
    - **遗留（建议单开一票）**：该测试的假向量维度（8）与真实 ChromaDB collection（1024）不一致，靠异常被 catch 兜底。不影响断言（断言锚定 lane+session 查 SQLite），但测试替身保真度欠账。
    - **诊断弯路备忘**：曾据 `gate/decision.py` 也有未替身 embed 而改那里，后经 socket 全屏蔽 + tripwire 实证该路径在本测试不触发，已回退。**网络逃逸必须 spy/tripwire 实证，不能读代码猜。**
  - **同例二次根因：测试依赖本地 `.env` 的非默认配置，CI 没 `.env` 故红（2026-09-27，run 36263359841）**：
    - 补了 embed patch 后网络逃逸警告消失（实证生效），但**同一断言仍红**——是两个独立问题。
    - **根因**：distill 候选的 `extractor_confidence = 0.3`，而 `GATE_MIN_EXTRACTOR_CONF` 的**代码默认值是 0.55**。CI 无 `.env` → 用默认值 → `decide()` 判 low confidence 拒掉候选（走 `pending_review`，宁 miss 不脏写）→ 无提案 → 无 `MemoryItem` → 断言红。本地 `.env` 是 `0.25`（注释「落地校准：Qwen2.5-7B 实际置信度 0.3-0.5」）→ 一直绿。
    - **实证（变异验证）**：`GATE_MIN_EXTRACTOR_CONF=0.55` 强制跑该测试 → failed；加 `patch.object(settings, "GATE_MIN_EXTRACTOR_CONF", 0.25)` 后同条件 → passed。即**这个测试一直在靠本地 `.env` 的一个非默认值活着**。
    - **修法**：测试内显式钉住阈值，同 `tests/test_e2e.py:165` 既有口径——那里注释本就写着「避免被宿主 .env 的 GATE_MIN_EXTRACTOR_CONF 污染」，**同样的坑踩过一次并修过一次，这次漏了 distill 这条路径**。
    - **系统性隐患（建议单开一票）**：本地 `.env` 有 14 项覆盖，至少两项让测试行为偏离 CI 默认——① `GATE_MIN_EXTRACTOR_CONF=0.25`（本轮已实证）；② `LANTAI_HOME` 指向**真实开发数据目录**，测试因此读写真实 ChromaDB/SQLite 而非隔离临时目录（本轮撞到 `Embedding dimension 8 does not match collection dimensionality 1024`），CI 是干净目录；另有 `RERANKER_ENABLED=false`、`OPENAI_BASE_URL`/`OPENAI_API_KEY` 亦不同。应收敛为「fixture 钉住全部相关配置」或「CI 显式注入同一份测试配置」，否则「本地绿 CI 红」会一而再。
  - **收口：CI 全绿（2026-09-27，run 36265312167）**：Tests job **success**，`1263 passed` + 遗忘质量门禁 **PASS**。这是兰台历史上 CI **第一次真正跑完全量 pytest 并通过**——v0.22.1 及以前该 job 51 秒就挂在 Ruff lint 步骤，全量测试与遗忘门禁从未在 CI 执行过。三次 push 各修一层「本地绿 CI 红」的不同机制：`bd973f6` naive datetime 债 + 依赖上界（37 failed）→ `667eb6dc` `_distill` 漏 patch embedding、store 路径真连网（1 failed）→ `14ed7d8` 测试靠本地 `.env` 的 `GATE_MIN_EXTRACTOR_CONF=0.25` 活着、CI 用默认 0.55 拒掉候选（1 failed）。三条机制互不相同，共同点是**本地环境与 CI 的隐式差异**（本地有真 key、有 `.env` 覆盖、指向真实数据目录）。

## [0.22.1] - 2026-09-26 - 起复（Qifu · 巩固撤销与碎片恢复 + 裁决时刻口径）

> 版本代号「起复」：唐宋典制「夺情起复」——官员去位（丁忧）后重新起用；被折叠的碎片记忆恢复现役即起复。贴合本版主题：为巩固产物补上对称的撤销面。命名依据见 [ADR-0052](docs/adr/0052-consolidation-revive.md)，登记见 [ADR-0013](docs/adr/0013-naming-system.md) §7 与 [CONTEXT.md](CONTEXT.md) 词汇表。
>
> 本版为 v0.22.0 的修订版：无新用户功能，收口 [ADR-0050](docs/adr/0050-consolidation-audit-gate.md) 决策 5 登记的两笔欠账（巩固回滚不闭环 / 冷却期起算点用 `created_at` 近似）。三票据 `.scratch/consolidation-rollback/`（票 01 服务函数 → 02 出口 → 03 decided_at 列）。

### Added
- **起复——巩固撤销与碎片恢复（2026-09-26，ADR-0052；票据 `.scratch/consolidation-rollback/issues/01-p0-consolidation-revive.md`；闭环 ADR-0050 决策 5 欠账①「retract 后碎片恢复只剩手改 DB」）**：
  - **服务函数** `lantai/services/record_ops_service.py:revive_consolidated(memory_id, *, reason, actor="", session=None)`：输入二义性按形态判别（宁 miss 不脏写，不猜意图）——主记忆（`source_ids` 非空，promoter 巩固 apply 落库标记）→ 撤销全簇（主记忆 retracted + 全部 consolidated 碎片恢复 active + 删除 supersedes 边 + 主记忆/逐碎片审计，一个事务）；碎片（`status == "consolidated"`）→ 恢复 active + FTS/向量重同步 + checkpoint（`trigger="revive"`）+ 审计（`action="revive"`）；两者皆非 → `{"ok": False, "error": "invalid target ..."}`（路由层映射 409）。**已被普通撤回过的主记忆同样受理**——补完没收尾的撤销（重做索引清理并如实回报，不假设干净）。
  - **独立函数不改 `rollback`**：`promoter.rollback` 是 5 类提案共用的单实体版本回滚，塞入「撤边 + 恢复他者」会让正确性风险外溢到 merge/deprecate 分支；巩固撤销形态（一主多碎片 + supersedes 边）是 consolidation 独有，独立最诚实。
  - **幂等**（同 `retract_memory` 先例，只认状态不假设首次同步结果）：簇已撤销（主记忆 retracted 且边清零、无 consolidated 碎片）→ `already_revoked`；碎片已 active 且带 `trigger="revive"` checkpoint（起复的精确标记，区别于普通 active 记忆与晚更正 supersedes 旧值）→ `already_active`。
  - **恢复语义的如实边界**：碎片折叠不改内容（consolidated 只改 `status`），恢复是真实的；但折叠后碎片若被遗忘/笔削/晚更改变更过，恢复的是变更后现状——不宣称「恢复到巩固前现场」（生成时刻基线由提案 `proposed_patch`/`provenance` 承载，与 ADR-0050 决策 3 同口径）。
  - **常量与审计面**：`STATUS_CONSOLIDATED` 入模块常量；`AUDIT_ACTIONS` 增补 `"revive"` / `"unconsolidate"`（既有六名不动，顺序追加），既有审计查询面自动可见。
  - **命名**：「起复」（Qifu，唐宋典制「夺情起复」——官员去位后重新起用；碎片被折叠如官员去位，恢复现役即起复）已登记 `CONTEXT.md` 词汇表；候选「拾残」因与既有「拾遗」（检索韧性降级）同字不同义违反 R5 而弃用。
  - **测试增量**：`tests/test_record_lifecycle.py` 新增 `TestReviveConsolidated` 13 例不 mock 冒烟（真 DB + 真 FTS + 真实服务函数，替身边界仅 embed 与向量库）：碎片起复三面可召回/审计无正文/checkpoint 留痕、撤销全簇三面 0 命中 + 碎片全 active + 边清零、审计双面、两形态幂等、普通 active 记忆被拒、普通撤回过的主记忆补完撤销、撤销后同提案再 apply 被提案状态机拒（封死双主记忆）、FTS/向量失败如实回报且 SQL 权威过滤面兜底。**变异验证**：去掉边清理 → 3 例 failed；跳过碎片恢复 → 4 例 failed（还原后全绿）。
- **起复出口——REST + MCP（2026-09-26，票据 `.scratch/consolidation-rollback/issues/02-p0-revive-exports.md`；ADR-0052；依赖票 01）**：
  - **REST** `POST /terminal/memory/{memory_id}/revive-consolidated`（`routes_terminal.py`，笔削家族同址）：reason 必填非空（422 on empty，同 retract 口径——撤销/恢复均须留痕）；归属校验复用 `_check_ownership`（P0 票 04 单一真源）；`memory not found` → 404、`invalid target` → 409 映射同既有范式；幂等语义标注于 docstring。
  - **MCP 工具** `revive_consolidated`（`mcp.py`）：schema 同 rollback 风格（memory_id + reason，两者均 required）；handler 调 service 单一真源；空 id/空 reason → `-32602` 且不调 service（留痕强制，异常隔离范式同既有）；`tools/list` 计数断言 59→60 同步。
  - **测试增量**：`tests/test_record_lifecycle.py` 新增 `TestReviveRoutes` 7 例（碎片起复 200 / 主记忆撤销 200 + 簇内碎片全 active + 边清零 / 非巩固目标 409 资源原样 / 空 reason 422 资源原样 / 越权 403 资源原样 / 不存在 404 / 重复撤销 already_revoked）；`tests/test_mcp.py` 新增 2 例（合法输入透传返回 / 空 id 与空 reason 双校验）。**变异验证**：删路由 → 6 例 failed；去 reason 强制 → MCP 校验例 failed（还原后全绿）。
- **提案裁决时刻列 `decided_at` + 冷却期口径修正（2026-09-26，ADR-0053；票据 `.scratch/consolidation-rollback/issues/03-p0-decided-at-column.md`；闭环 ADR-0050 边界「冷却期起算点用 `created_at` 近似」条）**：
  - **Schema**：`MemoryProposal.decided_at: datetime | None = None`（nullable 无默认——NULL 是「未记录」的事实状态，宁 miss 不猜；与 `applied_at`【apply 执行时刻】正交）。
  - **迁移 v23→v24**（`db.py`，`CURRENT_SCHEMA_VERSION` 23→24）：`_has_column` 幂等守卫 + `ALTER TABLE memoryproposal ADD COLUMN decided_at DATETIME`；**老行不回填**（拿 created_at 冒充 decided_at 会让冷却起算点悄悄失真，且把「猜」写进库不可撤销）；无索引（不参与检索热路径，只服务冷却判定）。
  - **三处落点**（裁决即落时刻，与终态同事务）：`decide_proposal` approve 分支、同函数 reject 分支、`promoter.apply_proposal` stale 硬门落 REJECTED 处（ADR-0050 决策 3 硬门＝系统裁决，与人工 reject 同口径）。
  - **冷却口径修正**：`_consolidation_proposal_blocked` 改读 `decided_at or created_at`——新行精确起算，老行自动回退旧口径（**既有 dedup/cooldown 用例零改动通过即证**，行为逐字节不变）。修复场景：提案 pending 逾冷却期后方被拒，旧口径下冷却窗口自生成时刻起算早已过期，次夜即重复提纯再奏（一次多余 LLM 成本 + 一次打扰）。
  - **测试增量**：`tests/test_migrations.py` 新增 v23→v24 两例（老库加列不填充 + 新库跳过不覆盖）；`tests/test_genglou_migration.py` 版本锚 23→24 顺延；`tests/test_consolidation.py` 新增 3 例（冷却按 decided_at 起算 / 老行回退 created_at 口径不变 / decide_proposal 两分支均落值且与 applied_at 正交）。**变异验证**：去 decided_at 落值 → 裁决用例 failed；冷却读侧忽略 decided_at → decided_at 用例 failed（还原后全绿）。

## [0.22.0] - 2026-09-26 - 圭表（Guibiao · 更漏双时间轴 + 宿主矩阵 + 沉潜过审）

> 版本代号「圭表」：古代度量日影定时辰的仪器——量时而立，与「更漏」同属计时器，贴合本版时间主线。登记见 [ADR-0013](docs/adr/0013-naming-system.md) §7 与 [CONTEXT.md](CONTEXT.md) 词汇表。

### Added
- **更漏波全量落地（2026-09-25，roadmap-v2-execution 票 03/04/05/08/09/10/11；ADR-0048/0049；「更漏」Genglou=铜壶滴漏，ADR-0013 转正登记）**:
  - **事件时间双时间轴（票 05-A/08/09/10，ADR-0048）**：MemoryItem 增 `event_time`（可空+精度 year~fuzzy，宁 miss 不猜）+ `valid_from`（语义必填，迁移回填 created_at）/`valid_to`；迁移链 v20→v21（`_has_column` 幂等 + 回填 + 三索引 + 表存在/双列守卫）。写入侧 `lantai/core/time_precision.py`（I1 校验 + 显式时间确定性格式提取，相对时间解析另票）+ 候选 provenance→proposal→MemoryItem 链路透传 + correct 可更正事件时间（旧值 corrections 留痕）。检索侧 `lantai/retrieval/temporal.py` 纯函数（逐精度区间/second 闭点特判/as-of 判定按信息量取最具体）+ hybrid 双挂点（主路径+拾遗降级）+ `RetrievalParams.temporal_asof_strict/temporal_fuzzy_penalty`（fail-closed）+ `POST /search` 与 MCP `search` 增 `as_of/as_of_recorded/time_from/time_to`（缺省行为逐字节不变）。迟到更正闭环 `lantai/cognition/late_correction.py`（替换型四步一个事务：supersedes 边+知命 SUPERSEDED+valid_to/valid_from 锚定+checkpoint+`ConflictEvent(kind="override")` 落账，I3 钳制豁免——锚点早于回填 valid_from 时钳制并留 clamped/anchor_raw；失效型仅回写 valid_to；自始错误导流笔削）；直断 recency 优先 event_time 且 `recency_axis` 入 DecisionTrace——「不按最近写入机械取胜」。**E2 实测：37 条时间/更新用例双视图证据选择正确率 1.0（≥90% 口径达标）**。
  - **E1 阶段化评测 harness（票 03）**：`lantai/eval/staged.py` 六段回放（提取→闸门→入库→索引→召回→注入）+ 首错归因 Q1-Q6 + 条件化计分（失败只计入首错段）+ 预埋锚点三件套（闸门必拒/索引前删档/改写零召回）各归正确阶段不串段 + `scripts/run_staged_eval.py` 报告出口；`tests/test_staged_eval.py` 含 run_dry_run 对照校验（阶段化不改评分定义）。
  - **回执链一等化（票 04，ADR-0049）**：RetrievalEvent 增 `request_id/receipt_status/receipt_at` 三列 + 迁移 v21→v22；状态机 pending→acked（backfill 落定）/pending→missed（`mark_missed_receipts` 超时惰性判定，missed 是事实不是错误）；`receipt_traceability_report()` 可追溯率出口 + `scripts/receipt_report.py`；shell_hook NDJSON `context` 响应携 `request_id`、`backfill` 帧透传对账（request_id 不一致记日志不拒绝，归属以 event_id 为准）。**受控链可追溯率 1.0 实测**。
  - **测试增量**：test_genglou_migration（4）/test_genglou_ingest（8）/test_genglou_retrieval（11）/test_genglou_correction（4）/test_staged_eval（4）/test_receipt_chain（7）/test_e2_temporal（3）共 41 例不 mock 冒烟；既有 test_migrations/test_shell_hook/test_retrieval_log 回归全绿。
  - **已知限制（如实声明）**：SQLModel 0.0.42 对 `default_factory` 字段（valid_from）在 DB 重读 NULL 时重新应用默认——「valid_from IS NULL」无稳定读回语义，I4 的承载面为 event_time IS NULL（E2 用例按此口径）；fuzzy 精度区间定宽 ±1d；事务轴完整 system-time as-of（append-only 版本化）留 v2（`as_of_recorded` 以 created_at 近似）。
- **宿主矩阵（2026-09-26，票据 `.scratch/host-matrix/`（spec + issues 01-05）；父票 `.scratch/roadmap-v2-execution/issues/06-p1-host-matrix.md`；roadmap P1-2；不起新名——描述性短语「宿主适配层/宿主矩阵」，扩写既有 Shell Hook 条）**:
  - **协议归一化层**（`lantai/integrations/host_protocol.py`，纯函数、无 IO）：`HostRequest` 不可变值对象 + `parse_host_request` + `render_host_response`；`scripts/shell_hook.py` 退化为「适配 → 归一化 → 分发 → 渲染」的宿主实现之一，`_handle_one` 签名与返回逐字节不变——`tests/test_hermes_plugin.py` **零改动**通过、`tests/test_shell_hook.py` 既有断言**零改动**通过（该文件仅新增 2 条畸形帧回归测试，未触碰既有 24 例）。
  - **宿主帧适配**（`lantai/integrations/host_adapters.py`）：Hermes 直通；Claude Code 与 Codex CLI → `hookSpecificOutput.additionalContext`（官方文档一手实证二者同形状）。**只翻译注入类响应**，回执/对话/底本等控制面应答原样返回（否则 `receipt_status` 被抹掉）。
  - **≥3 宿主端到端冒烟**（`tests/test_host_matrix.py`，7 passed）：Hermes + Claude Code + Codex，真实子进程 + 真实 stdin/stdout NDJSON 协议帧 + 真实 SQLite/FTS，每宿主跑「注入（context 非空 + event_id）→ 回执（`receipt_status="acked"`）→ 隔离（畸形帧降级不影响后续）」三断言。唯一替身为子进程外部 embedding 网络（`tests/support/host_matrix_stub/sitecustomize.py`，确定性 3-gram 哈希；子进程无法继承父进程 patch）。
  - **安装出口**（`scripts/install_host_hooks.py`）：默认**只打印**配置片段、不写宿主目录（宁 miss 不脏写）；`--write` 才落盘并打印落点；**落点已存在则拒绝覆盖**。三宿主片段：`.claude/settings.json` / `.codex/hooks.json`（含 `features.hooks`）/ `.cursor/rules/lantai.mdc`。
  - **Cursor 降级档（如实声明）**：调研实证 Cursor **无命令钩子入口**，只能静态规则注入（无运行时检索、无回执）——故**不计入命令钩子矩阵**，矩阵取 Hermes + Claude Code + Codex（三者同构）。不依赖 Codex 阻塞语义（官方 config-reference 未载）。
  - **协议文档**（`docs/host-hook-protocol.md`）：5 动作逐条列字段/超时/降级/回执语义；与 ADR-0006（时点决策）分工，ADR 只加引用行。
  - **两处真实缺陷（实施中发现并修）**：①非对象 JSON 帧（`[1,2]`/`null`/`123`）原会抛 `AttributeError`——`--serve` 常驻 NDJSON 循环里**一个畸形帧即打死进程**，今统一静默降级；②CC/Codex 响应适配最初连回执应答也包成 `additionalContext`，致 `backfill` 在这两个宿主上**恒失败**——E2E 暴露后修正为只翻译注入类响应。
  - **已知不一致（如实登记，未改）**：`checkpoint_write` 的 `session_id` 不过归一化函数（`query`/`dialogue` 经）——既有差异，改它会变更已落库来源链值；记入协议文档 §2.4。
- **沉潜产物过审（2026-09-26，票据 `.scratch/roadmap-v2-execution/issues/07-p1-consolidation-audit.md`；ADR-0050；不起新名——描述性短语「沉潜/巩固产物过审」，`ProposalStatus.SHADOW`/`CONSOLIDATION` 为技术枚举值不入词汇表，CONTEXT.md 零改动）**:
  - **三模式机制（ADR-0050 决策 1/2）**：settings 增 `CONSOLIDATION_AUDIT_MODE`（off/shadow/enforce，默认 off＝现行直写行为逐字节零漂移，off 冒烟断言零新行；非法值 fail-loud 不静默回落——拒绝执行本周期巩固＋ERROR 留痕＋report status="refused"，宁巩固停摆不静默直写）＋ `CONSOLIDATION_SHADOW_MAX_DAYS=7`（shadow 硬时限，自 ConsolidationRun 首条 mode=shadow 留痕起算，超期拒绝执行巩固——静默直写不能在过审制名义下无限合法存续）＋ `CONSOLIDATION_REJECTED_COOLDOWN_DAYS=30`（拒绝冷却，只抑制生成侧重复奏不触碰提案终态）。
  - **enforce 提案制（决策 3）**：`consolidate_cluster` 提纯+TrustMem 通过后只落恰一条 pending `MemoryProposal`（`ProposalType` 枚举增补 `CONSOLIDATION`；evidence_ids=source_ids、proposed_patch=主记忆构造全集、confidence=提纯 confidence【漏填将令 supersedes 边与案牍徽标显 0.0】、tenant/user/agent/session 四元组取首碎片【同簇未必同租户】、reason=TrustMem 校验结论、decided_by="consolidation"），裁决前不落主记忆不折叠零 checkpoint；生成侧幂等去重＋拒绝冷却（skipped_dupes/skipped_rejected_cooldown/skipped_lowq 单列入 report 与留痕，防每夜重复提纯的真实 LLM 成本）；提案无 TTL（宁巩固延迟不未审生效）。
  - **裁决 apply（决策 3/5）**：`promoter.apply_proposal` 显式 `elif proposal_type == "consolidation"` 分支插在 add 捕获分支之前（落锚测试固化：apply 后不得出现 trigger="gate"/"evolve" checkpoint）——一个事务内主记忆落 active（构造取 proposed_patch 不重推断，before={} 仿 add 先例）＋碎片折叠＋supersedes 边（方向同 merge 分支，confidence=提案 confidence）＋逐条真实 id checkpoint（trigger="consolidation"、全带 proposal_id；碎片 before＝apply 时刻实际现状非「巩固前现场」）＋主记忆 embed/向量/FTS 同步＋碎片 FTS/向量清理（仿 merge，收紧口径）；evidence 三分处置（仍 active→折叠+边+checkpoint；已 archived→仅补血缘边无 checkpoint；已删除→不建边，缺口入 apply 返回与日志）；stale 硬门：任一 evidence 已 consolidated 即拒绝 apply（封死 off 回切双主记忆与重叠集双活两变体）；reject 复用既有强制 reason 与终态落库，碎片原样零索引变更。
  - **shadow 影子对照（决策 4）**：直写+折叠全链照旧，另落恰一条 `ProposalStatus.SHADOW` 影子提案（纯枚举增补无迁移；decided_by="shadow"、provenance 对账记 master_id/sources）＋该次伪 id checkpoint 的 proposal_id 填影子提案 id（生成留痕↔产物对账键）；影子行双门禁结构性不可裁决不可应用（decide 仅受理 PENDING、apply 仅受理 PENDING/APPROVED）、不进 pending 裁决队列。
  - **验收统计出口（决策 9）**：新表 `ConsolidationRun` 运行留痕（shadow/enforce 期每次运行一行，迁移 v22→v23，off 期零新行；仿 ReflectRun 范式）＋ `consolidation_audit_report()` 查询件＋ `scripts/consolidation_audit_report.py` CLI——enforce 自证①②合取（窗口内创建的 consolidation 提案数÷留痕 purified_ok 恰 100%【留痕↔提案两路一致性】＋窗口内伪 id checkpoint 新增行数必须为 0【独立于两路的直写指纹】，仅①同路径写两表 bug 不可见、仅②绕过两路直写不可见）、shadow 三方互证（伪 id checkpoint 行数＝影子提案数＝purified_ok）、无样本比例返回 None 不编造；run report 增 proposals_created/mode/skipped_* 增量键，new_memories 固定「新落主记忆数」（enforce 期字面 0），status 判定扩展为 (new_memories+proposals_created+pruned)>0 防只产提案误报 idle。
  - **案牍可见性**：consolidation 提案按 status=="pending" 捞取自动进案牍待审（无类型白名单），展示层增补「沉潜巩固提案」中文文案与风险标注（折叠多碎片与 merge/deprecate 同级 high）；无自动应用旁路（evolve_worker 仅 apply APPROVED、持节仅扫 pending_review 候选，已核验入 ADR 边界）——consolidation pending 提案只能人工裁决。
  - **测试增量**：test_consolidation.py 按三模式逐条改写并注明理由（18 例不 mock 冒烟：off 零新行/enforce 生成→apply→拒绝真实全链+FTS/向量/checkpoint 断言/evidence 三分/stale 硬门/shadow 双门禁/去重冷却/非法值 fail-loud/shadow 超期/验收统计含 CLI）；test_migrations.py 增 v22→v23 用例；test_genglou_migration 版本锚 22→23 顺延；既有 REST/MCP 报告形状断言保留（既有键向后兼容，新键为增量）。
  - **反思过审收口（决策 7a，维护者 2026-09-26 显式拍板）**：增设 `REFLECT_AUTO_APPLY: bool = False` 总开关——默认关＝反思产物一律进 pending 待人工裁决（与沉潜/autodream 同轨：后台合成产物不自动生效）；置 True 恢复「高置信 + rejecter risk=low 自动 apply」既有通道（`REFLECT_AUTO_APPLY_CONF=0.7` 仅开启时生效）。消费点唯一（`reflector.py:395`），`digest_worker` 月度盘点「待回填结论 B」文案同步改写（否则盘点失真）。**至此 roadmap P1-3「沉潜/反思产物一律过审」全链闭环，结项前置条件解除。**
  - **零硬编码与效率整改**：`find_consolidation_clusters` 聚合主记忆判定阈值原硬编码 `3` 提取为 `CONSOLIDATION_AGGREGATE_MASTER_MIN_SOURCES: int = 3`（ADR-0002）；`_consolidation_proposal_blocked` 状态过滤下推 SQL（只取 pending/rejected 两态，applied/shadow 行与判定无关却永久累积，原全表取回再于 Python 内过滤的代价随时长线性增长）；`work_item_service` 提案分支消除同一 id 的重复 `s.get`。
  - **终审四项（代码审查两轴整改）**：①`promoter.apply_proposal` stale 硬门原「早退不改状态」造成 **livelock**——`decide_proposal` 已置 APPROVED 而 `run_pending_proposals` 专捞 APPROVED 每轮重试每次失败（独立探针证实）；改为落终态 `REJECTED` + `decision_reason` 落痕 + commit（ADR-0050 决策 3「拒绝该提案」的终态语义），探针复验状态不再回捞；②`_LAST_CONSOLIDATION_REPORT` 初值仅 5 个旧键而 REST/MCP 直接透传该 dict——首运行前读新键即 KeyError，初值键集补齐至与 ADR 决策 3 报告契约一致；③`enforce.self_attestation_ok` 合取键（`(提案数==purified_ok) and (伪id行数==0)`，分母 0 时 None）——ADR 决策 9 明言①②合取方唯一可实现，原仅暴露两个分键，消费方只看 `ratio_ok` 会在直写指纹违约时误判「通过」。另：非法值拒绝路径补落 `ConsolidationRun` 留痕行（与 shadow 硬时限拒绝同轨；mode 记原非法值），拒绝事件不再只有内存 report 与 logger。
  - **已知限制（如实声明）**：单次 apply 各实体仅 1 笔 checkpoint（<2 笔回滚门槛）且 rollback 无 supersedes 补偿——改善为「真实 id 有留痕可审计」但不宣称「rollback 恢复可用」；碎片恢复无工具（笔削六操作不含 consolidated→active、unarchive 仅 archived），期内下线只能 retract 主记忆止召回＋手改 DB，完整回滚与恢复语义另票；off/shadow 期碎片 FTS/向量照旧不清（两代语义并存至全量 enforce）；重叠集并行 pending 提案由 apply 硬门拒后至者显式留痕；**冷却期起算点用 `created_at` 近似**——`MemoryProposal` 无裁决时刻列（`applied_at` 仅 apply 时写、reject 不写），提案 pending 逾冷却期后方被拒则该窗口内冷却失效（次夜重复提纯与打扰一次）；不脏写属「宁 miss」方向降级，修法须新增 `decided_at` 列＋迁移（v23→v24）另票。

### Fixed
- **沉潜零硬编码收尾（2026-09-26，ADR-0002）**：`find_consolidation_clusters` 的 `min_cluster_size`（原签名默认值 `3` + `run_consolidation_cycle` 调用字面量两处）与 `prune_decayed_synapses` 的 `threshold`（原签名默认值 `0.05` + 调用字面量两处）提取为 settings `CONSOLIDATION_MIN_CLUSTER_SIZE: int = 3` / `CONSOLIDATION_PRUNE_THRESHOLD: float = 0.05`，签名默认值改 `None` 惰性取 settings（显式传参行为不变）；两例不 mock 冒烟（`test_min_cluster_size_is_configurable` / `test_prune_threshold_is_configurable`）以「调参前后同一输入行为反转」锚定参数真实生效，变异验证（还原硬编码字面量 → 两例均 failed）证明测试不空转。
- **终验整改三连（2026-09-26，genglou 票 09/11）**：①ConflictEngine diffs 只算数值键（recency_axis 字符串入差值致 decide 全链炸）②asof_matches 判定顺序按信息量取最具体且 I4 承载面定为 event_time IS NULL（SQLModel 0.0.42 default_factory 读回语义下 valid_from 无稳定 NULL，如实入 CHANGELOG 已知限制）③test_infra 的 chromadb patch 改 sys.modules 条目替换（全局模块 setattr 污染内部组件缓存泄漏至后续测试，全量实证）+ 评测 embed 统一确定性 hash（杜绝 1024/512 混维）；全量门禁 1138 passed + 遗忘质量 PASS
- **现状审阅六步收口（2026-09-20 第三批，票据 `.scratch/state-remediation-20260920/`）**:

### Added
- **宿主矩阵（2026-09-26，票据 `.scratch/host-matrix/`（spec + issues 01-05）；父票 `.scratch/roadmap-v2-execution/issues/06-p1-host-matrix.md`；roadmap P1-2；不起新名——描述性短语「宿主适配层/宿主矩阵」，扩写既有 Shell Hook 条）**:
  - **协议归一化层**（`lantai/integrations/host_protocol.py`，纯函数、无 IO）：`HostRequest` 不可变值对象 + `parse_host_request` + `render_host_response`；`scripts/shell_hook.py` 退化为「适配 → 归一化 → 分发 → 渲染」的宿主实现之一，`_handle_one` 签名与返回逐字节不变——`tests/test_hermes_plugin.py` **零改动**通过、`tests/test_shell_hook.py` 既有断言**零改动**通过（该文件仅新增 2 条畸形帧回归测试，未触碰既有 24 例）。
  - **宿主帧适配**（`lantai/integrations/host_adapters.py`）：Hermes 直通；Claude Code 与 Codex CLI → `hookSpecificOutput.additionalContext`（官方文档一手实证二者同形状）。**只翻译注入类响应**，回执/对话/底本等控制面应答原样返回（否则 `receipt_status` 被抹掉）。
  - **≥3 宿主端到端冒烟**（`tests/test_host_matrix.py`，7 passed）：Hermes + Claude Code + Codex，真实子进程 + 真实 stdin/stdout NDJSON 协议帧 + 真实 SQLite/FTS，每宿主跑「注入（context 非空 + event_id）→ 回执（`receipt_status="acked"`）→ 隔离（畸形帧降级不影响后续）」三断言。唯一替身为子进程外部 embedding 网络（`tests/support/host_matrix_stub/sitecustomize.py`，确定性 3-gram 哈希；子进程无法继承父进程 patch）。
  - **安装出口**（`scripts/install_host_hooks.py`）：默认**只打印**配置片段、不写宿主目录（宁 miss 不脏写）；`--write` 才落盘并打印落点；**落点已存在则拒绝覆盖**。三宿主片段：`.claude/settings.json` / `.codex/hooks.json`（含 `features.hooks`）/ `.cursor/rules/lantai.mdc`。
  - **Cursor 降级档（如实声明）**：调研实证 Cursor **无命令钩子入口**，只能静态规则注入（无运行时检索、无回执）——故**不计入命令钩子矩阵**，矩阵取 Hermes + Claude Code + Codex（三者同构）。不依赖 Codex 阻塞语义（官方 config-reference 未载）。
  - **协议文档**（`docs/host-hook-protocol.md`）：5 动作逐条列字段/超时/降级/回执语义；与 ADR-0006（时点决策）分工，ADR 只加引用行。
  - **两处真实缺陷（实施中发现并修）**：①非对象 JSON 帧（`[1,2]`/`null`/`123`）原会抛 `AttributeError`——`--serve` 常驻 NDJSON 循环里**一个畸形帧即打死进程**，今统一静默降级；②CC/Codex 响应适配最初连回执应答也包成 `additionalContext`，致 `backfill` 在这两个宿主上**恒失败**——E2E 暴露后修正为只翻译注入类响应。
  - **已知不一致（如实登记，未改）**：`checkpoint_write` 的 `session_id` 不过归一化函数（`query`/`dialogue` 经）——既有差异，改它会变更已落库来源链值；记入协议文档 §2.4。
- **沉潜产物过审（2026-09-26，票据 `.scratch/roadmap-v2-execution/issues/07-p1-consolidation-audit.md`；ADR-0050；不起新名——描述性短语「沉潜/巩固产物过审」，`ProposalStatus.SHADOW`/`CONSOLIDATION` 为技术枚举值不入词汇表，CONTEXT.md 零改动）**:
  - **三模式机制（ADR-0050 决策 1/2）**：settings 增 `CONSOLIDATION_AUDIT_MODE`（off/shadow/enforce，默认 off＝现行直写行为逐字节零漂移，off 冒烟断言零新行；非法值 fail-loud 不静默回落——拒绝执行本周期巩固＋ERROR 留痕＋report status="refused"，宁巩固停摆不静默直写）＋ `CONSOLIDATION_SHADOW_MAX_DAYS=7`（shadow 硬时限，自 ConsolidationRun 首条 mode=shadow 留痕起算，超期拒绝执行巩固——静默直写不能在过审制名义下无限合法存续）＋ `CONSOLIDATION_REJECTED_COOLDOWN_DAYS=30`（拒绝冷却，只抑制生成侧重复奏不触碰提案终态）。
  - **enforce 提案制（决策 3）**：`consolidate_cluster` 提纯+TrustMem 通过后只落恰一条 pending `MemoryProposal`（`ProposalType` 枚举增补 `CONSOLIDATION`；evidence_ids=source_ids、proposed_patch=主记忆构造全集、confidence=提纯 confidence【漏填将令 supersedes 边与案牍徽标显 0.0】、tenant/user/agent/session 四元组取首碎片【同簇未必同租户】、reason=TrustMem 校验结论、decided_by="consolidation"），裁决前不落主记忆不折叠零 checkpoint；生成侧幂等去重＋拒绝冷却（skipped_dupes/skipped_rejected_cooldown/skipped_lowq 单列入 report 与留痕，防每夜重复提纯的真实 LLM 成本）；提案无 TTL（宁巩固延迟不未审生效）。
  - **裁决 apply（决策 3/5）**：`promoter.apply_proposal` 显式 `elif proposal_type == "consolidation"` 分支插在 add 捕获分支之前（落锚测试固化：apply 后不得出现 trigger="gate"/"evolve" checkpoint）——一个事务内主记忆落 active（构造取 proposed_patch 不重推断，before={} 仿 add 先例）＋碎片折叠＋supersedes 边（方向同 merge 分支，confidence=提案 confidence）＋逐条真实 id checkpoint（trigger="consolidation"、全带 proposal_id；碎片 before＝apply 时刻实际现状非「巩固前现场」）＋主记忆 embed/向量/FTS 同步＋碎片 FTS/向量清理（仿 merge，收紧口径）；evidence 三分处置（仍 active→折叠+边+checkpoint；已 archived→仅补血缘边无 checkpoint；已删除→不建边，缺口入 apply 返回与日志）；stale 硬门：任一 evidence 已 consolidated 即拒绝 apply（封死 off 回切双主记忆与重叠集双活两变体）；reject 复用既有强制 reason 与终态落库，碎片原样零索引变更。
  - **shadow 影子对照（决策 4）**：直写+折叠全链照旧，另落恰一条 `ProposalStatus.SHADOW` 影子提案（纯枚举增补无迁移；decided_by="shadow"、provenance 对账记 master_id/sources）＋该次伪 id checkpoint 的 proposal_id 填影子提案 id（生成留痕↔产物对账键）；影子行双门禁结构性不可裁决不可应用（decide 仅受理 PENDING、apply 仅受理 PENDING/APPROVED）、不进 pending 裁决队列。
  - **验收统计出口（决策 9）**：新表 `ConsolidationRun` 运行留痕（shadow/enforce 期每次运行一行，迁移 v22→v23，off 期零新行；仿 ReflectRun 范式）＋ `consolidation_audit_report()` 查询件＋ `scripts/consolidation_audit_report.py` CLI——enforce 自证①②合取（窗口内创建的 consolidation 提案数÷留痕 purified_ok 恰 100%【留痕↔提案两路一致性】＋窗口内伪 id checkpoint 新增行数必须为 0【独立于两路的直写指纹】，仅①同路径写两表 bug 不可见、仅②绕过两路直写不可见）、shadow 三方互证（伪 id checkpoint 行数＝影子提案数＝purified_ok）、无样本比例返回 None 不编造；run report 增 proposals_created/mode/skipped_* 增量键，new_memories 固定「新落主记忆数」（enforce 期字面 0），status 判定扩展为 (new_memories+proposals_created+pruned)>0 防只产提案误报 idle。
  - **案牍可见性**：consolidation 提案按 status=="pending" 捞取自动进案牍待审（无类型白名单），展示层增补「沉潜巩固提案」中文文案与风险标注（折叠多碎片与 merge/deprecate 同级 high）；无自动应用旁路（evolve_worker 仅 apply APPROVED、持节仅扫 pending_review 候选，已核验入 ADR 边界）——consolidation pending 提案只能人工裁决。
  - **测试增量**：test_consolidation.py 按三模式逐条改写并注明理由（15 例不 mock 冒烟：off 零新行/enforce 生成→apply→拒绝真实全链+FTS/向量/checkpoint 断言/evidence 三分/stale 硬门/shadow 双门禁/去重冷却/非法值 fail-loud/shadow 超期/验收统计含 CLI）；test_migrations.py 增 v22→v23 用例；test_genglou_migration 版本锚 22→23 顺延；既有 REST/MCP 报告形状断言保留（既有键向后兼容，新键为增量）。
  - **反思过审收口（决策 7a，维护者 2026-09-26 显式拍板）**：增设 `REFLECT_AUTO_APPLY: bool = False` 总开关——默认关＝反思产物一律进 pending 待人工裁决（与沉潜/autodream 同轨：后台合成产物不自动生效）；置 True 恢复「高置信 + rejecter risk=low 自动 apply」既有通道（`REFLECT_AUTO_APPLY_CONF=0.7` 仅开启时生效）。消费点唯一（`reflector.py:395`），`digest_worker` 月度盘点「待回填结论 B」文案同步改写（否则盘点失真）。**至此 roadmap P1-3「沉潜/反思产物一律过审」全链闭环，结项前置条件解除。**
  - **零硬编码与效率整改**：`find_consolidation_clusters` 聚合主记忆判定阈值原硬编码 `3` 提取为 `CONSOLIDATION_AGGREGATE_MASTER_MIN_SOURCES: int = 3`（ADR-0002）；`_consolidation_proposal_blocked` 状态过滤下推 SQL（只取 pending/rejected 两态，applied/shadow 行与判定无关却永久累积，原全表取回再于 Python 内过滤的代价随时长线性增长）；`work_item_service` 提案分支消除同一 id 的重复 `s.get`。
  - **终审四项（代码审查两轴整改）**：①`promoter.apply_proposal` stale 硬门原「早退不改状态」造成 **livelock**——`decide_proposal` 已置 APPROVED 而 `run_pending_proposals` 专捞 APPROVED 每轮重试每次失败（独立探针证实）；改为落终态 `REJECTED` + `decision_reason` 落痕 + commit（ADR-0050 决策 3「拒绝该提案」的终态语义），探针复验状态不再回捞；②`_LAST_CONSOLIDATION_REPORT` 初值仅 5 个旧键而 REST/MCP 直接透传该 dict——首运行前读新键即 KeyError，初值键集补齐至与 ADR 决策 3 报告契约一致；③`enforce.self_attestation_ok` 合取键（`(提案数==purified_ok) and (伪id行数==0)`，分母 0 时 None）——ADR 决策 9 明言①②合取方唯一可实现，原仅暴露两个分键，消费方只看 `ratio_ok` 会在直写指纹违约时误判「通过」。另：非法值拒绝路径补落 `ConsolidationRun` 留痕行（与 shadow 硬时限拒绝同轨；mode 记原非法值），拒绝事件不再只有内存 report 与 logger。
  - **已知限制（如实声明）**：单次 apply 各实体仅 1 笔 checkpoint（<2 笔回滚门槛）且 rollback 无 supersedes 补偿——改善为「真实 id 有留痕可审计」但不宣称「rollback 恢复可用」；碎片恢复无工具（笔削六操作不含 consolidated→active、unarchive 仅 archived），期内下线只能 retract 主记忆止召回＋手改 DB，完整回滚与恢复语义另票；off/shadow 期碎片 FTS/向量照旧不清（两代语义并存至全量 enforce）；重叠集并行 pending 提案由 apply 硬门拒后至者显式留痕；**冷却期起算点用 `created_at` 近似**——`MemoryProposal` 无裁决时刻列（`applied_at` 仅 apply 时写、reject 不写），提案 pending 逾冷却期后方被拒则该窗口内冷却失效（次夜重复提纯与打扰一次）；不脏写属「宁 miss」方向降级，修法须新增 `decided_at` 列＋迁移（v23→v24）另票。
- **更漏波全量落地（2026-09-25，roadmap-v2-execution 票 03/04/05/08/09/10/11；ADR-0048/0049；「更漏」Genglou=铜壶滴漏，ADR-0013 转正登记）**:
  - **事件时间双时间轴（票 05-A/08/09/10，ADR-0048）**：MemoryItem 增 `event_time`（可空+精度 year~fuzzy，宁 miss 不猜）+ `valid_from`（语义必填，迁移回填 created_at）/`valid_to`；迁移链 v20→v21（`_has_column` 幂等 + 回填 + 三索引 + 表存在/双列守卫）。写入侧 `lantai/core/time_precision.py`（I1 校验 + 显式时间确定性格式提取，相对时间解析另票）+ 候选 provenance→proposal→MemoryItem 链路透传 + correct 可更正事件时间（旧值 corrections 留痕）。检索侧 `lantai/retrieval/temporal.py` 纯函数（逐精度区间/second 闭点特判/as-of 判定按信息量取最具体）+ hybrid 双挂点（主路径+拾遗降级）+ `RetrievalParams.temporal_asof_strict/temporal_fuzzy_penalty`（fail-closed）+ `POST /search` 与 MCP `search` 增 `as_of/as_of_recorded/time_from/time_to`（缺省行为逐字节不变）。迟到更正闭环 `lantai/cognition/late_correction.py`（替换型四步一个事务：supersedes 边+知命 SUPERSEDED+valid_to/valid_from 锚定+checkpoint+`ConflictEvent(kind="override")` 落账，I3 钳制豁免——锚点早于回填 valid_from 时钳制并留 clamped/anchor_raw；失效型仅回写 valid_to；自始错误导流笔削）；直断 recency 优先 event_time 且 `recency_axis` 入 DecisionTrace——「不按最近写入机械取胜」。**E2 实测：37 条时间/更新用例双视图证据选择正确率 1.0（≥90% 口径达标）**。
  - **E1 阶段化评测 harness（票 03）**：`lantai/eval/staged.py` 六段回放（提取→闸门→入库→索引→召回→注入）+ 首错归因 Q1-Q6 + 条件化计分（失败只计入首错段）+ 预埋锚点三件套（闸门必拒/索引前删档/改写零召回）各归正确阶段不串段 + `scripts/run_staged_eval.py` 报告出口；`tests/test_staged_eval.py` 含 run_dry_run 对照校验（阶段化不改评分定义）。
  - **回执链一等化（票 04，ADR-0049）**：RetrievalEvent 增 `request_id/receipt_status/receipt_at` 三列 + 迁移 v21→v22；状态机 pending→acked（backfill 落定）/pending→missed（`mark_missed_receipts` 超时惰性判定，missed 是事实不是错误）；`receipt_traceability_report()` 可追溯率出口 + `scripts/receipt_report.py`；shell_hook NDJSON `context` 响应携 `request_id`、`backfill` 帧透传对账（request_id 不一致记日志不拒绝，归属以 event_id 为准）。**受控链可追溯率 1.0 实测**。
  - **测试增量**：test_genglou_migration（4）/test_genglou_ingest（8）/test_genglou_retrieval（11）/test_genglou_correction（4）/test_staged_eval（4）/test_receipt_chain（7）/test_e2_temporal（3）共 41 例不 mock 冒烟；既有 test_migrations/test_shell_hook/test_retrieval_log 回归全绿。
  - **已知限制（如实声明）**：SQLModel 0.0.42 对 `default_factory` 字段（valid_from）在 DB 重读 NULL 时重新应用默认——「valid_from IS NULL」无稳定读回语义，I4 的承载面为 event_time IS NULL（E2 用例按此口径）；fuzzy 精度区间定宽 ±1d；事务轴完整 system-time as-of（append-only 版本化）留 v2（`as_of_recorded` 以 created_at 近似）。

### Fixed
- **现状审阅六步收口（2026-09-20 第三批，票据 `.scratch/state-remediation-20260920/`）**:
  - **conftest API_KEY 测试环境消毒（票 02）**：仓库根部署 `.env`（gitignored）经 settings 的 env_file 渗入测试进程，`dev_mode_allowed()` 全局拒绝 DEV MODE——29 文件 70 例 401。conftest 加 autouse fixture 清 `settings.API_KEY` + pin 测试（`tests/test_test_environment.py`）把「测试环境 API_KEY 恒空」钉成显式契约；裸跑 `pytest tests/ -q`（无任何环境前缀）恢复全绿。
  - **终端写路由守卫补全（票 03）**：`PUT /terminal/memory/{id}` 补归属校验（403，与 DELETE 同一 `ensure_can_delete` 真源同口径）——原先同资源删有守卫、改无守卫；retracted 拒改文（409）堵 D23「撤回后三面 0 命中」被改文旁路重新填回 FTS/向量的缺口；顺带修复 `updated_at` 赋 ISO 字符串致 PUT 任何成功更新必 500 的隐藏 bug（该路由此前零测试覆盖，新正面对照测试抓出）；CHANGELOG 票04「统一 403 不区分 404 防存在性探测」假宣称改正为如实表述（404 不存在 / 403 越权，REST 常规可区分）。
  - **插件注入读路径 session_id 透传（票 05）**：`_call_hook` 携带 session_id（shell_hook 服务端本就支持，纯发送端补线），Hermes 主环路检索事件落会话——「带 session 的读才算真实会话读」从写路径半程补齐全链；缓冲序号 docstring 对齐 fd03b184 后的单调计数语义。无会话行为不变（空串照发，服务端归一化为 NULL）。
  - **BM25 AND 语义如实声明（票 04，docs 零行为变更）**：更正 8834f846「确定性 AND 路径零改动」假宣称——默认开档下 search_fts 与 OR 召回面共用 `_bm25_keywords`，AND 语义为「各 gram 任意位置全命中」，gram 跨位拼合假阳性面如实写明（fts.py/settings.py/测试 docstring/CHANGELOG 四处）；`_GRAM_TERMS_MAX` 定义前移；Whitepaper 司天 ADR 0044→0045 误引修正。评测门数值不动（typo_mid 1.0 等 6/6 PASS）。

### Added
- **笔削（Bixiao，撤回/删除四分法，2026-09-19 第三批；roadmap-v2 P0-2 / 调研 D23；命名登记 CONTEXT.md，《史记》「笔则笔，削则削」；ADR-0047）**:
  - **四分语义**：纠错 correct（就地改文保留版本历史，旧文进 `provenance.corrections`）、撤回 retract（主张停用即全检索面禁用，不可自动复活，unretract 仅 admin）、归档 archive（可逆退出常规检索，FTS/向量行保留复原零成本）、删除 delete（净清除正文，仅留无正文审计）。服务单一真源 `lantai/services/record_ops_service.py`，路由 `POST /terminal/memory/{id}/retract|unretract|archive|unarchive|correct`。
  - **验收口径落地（D23）**：确定性用例「撤回后禁用命中=0」——SQL status / FTS / 向量三面 0 命中（`tests/test_record_lifecycle.py` 21 例不 mock 冒烟）；FTS 清理失败注入测试证明响应如实上报且 SQL 权威过滤面兜底仍 0 命中（宁 miss 不脏写）。
  - **无正文审计**：新表 `memory_audit_events`（`MemoryAuditEvent`）——六操作全枚举留痕，只记 content_hash/长度/版本，永不存正文（隐私删除后唯一痕迹，铁律）。
  - **不复活约束**：retracted 不被沉潜/晋升等任何 worker 翻回 active（锚测试固化）。
  - **双轴审查整改**：向量重同步 `embed()` 取值漏 `[0]`（三层嵌套必炸，替身加形状守卫锚定）+ metadata 补齐 8 键归属契约（缺键会让属主过滤检索永久丢失该条，守卫锚定）；归档仅 active 入口（candidate 不得绕晋升闸门）；纠错改文 FTS 失败随事务回滚（不留「可命中旧文」脏索引，与 PATCH 同策略）；retract 幂等分支不再假报同步成功；delete 审计 best-effort 如实进 warnings。

### Fixed
- **三处索引同步静默失败（同 except-pass 家族，笔削票据 05 附带发现）**：删除路由 import 不存在的 `remove_fts` → ImportError 被吞，FTS 清理从未生效；更新路由调向量库不存在的 `vs.update` → AttributeError 被吞，向量重同步从未生效；更新路由给 `sync_fts` 传 driver 连接而非 Session → 同样被吞。现 FTS 同事务同步（ADR-0008 强一致，失败随事务回滚）、向量 best-effort 且结果如实进响应（`fts_removed`/`vector_synced`/`warnings`），不再假装干净。

### Added
- **遗留问题清理（2026-09-19 第二批，票 06/07）**:
  - **FTS BM25 3-gram 滑窗分词（票 06）**：OR+bm25 召回路径原按空白切词，中文整句退化为单个短语匹配——词中错字/词面重叠改写全部零召回（基线实测）。改 CJK 3-gram 滑窗 + ASCII 整词（`_bm25_keywords` 单一真源，`FTS_BM25_GRAM_TOKENIZE` 默认开，off=旧语义对照）：错字只污染个别 gram，共享词根即部分命中。**typo_mid 0→1.0（GATES 增补确定性门 1.0）**、paraphrase 0→0.25（词面重叠型，维持报告型——完全改写归向量层）；AND 确定性路径（search_fts）共用同一关键词源，默认开档下语义同步松化为「各 gram 全命中」而非旧整句短语——已知假阳性面为 gram 跨位拼合（现状整改票04 如实声明，五项既有门以新语义验收不动）；顺带清除 search_fts 遗留 debug print。
  - **依赖 advisory 处置留痕（票 07）**：pip-audit 定位 Mimosa 离线 advisory 匹配项——chromadb 0.6.3 ×3（PYSEC-2026-3813/3814/3815，修复仅在 1.x 重大重写版）。处置：wontfix 留痕——兰台仅内嵌 PersistentClient（无服务端/RBAC 面、单用户本地），pyproject 注释留证，1.x 迁移登记独立专项。

### Added
- **P0 可靠性自证 + 宿主闭环（2026-09-19，方向调研 `docs/research/agent-memory-development-directions-2026-09.md`，票据 `.scratch/p0-reliability-host-loop/`）**:
  - **宿主来源链贯通 + 注入回执（票 02）**：Hermes 插件缓冲条目携带会话内 `turn` 序号，flush 逐条 `{type:dialogue, text, session_id, turn}` 透传到候选 `session_id` 列 + `provenance.origin_turn`（缺会话留空、缺轮次 None，宁 miss 不脏写）；检索注入成功后按 `event_id`+`evidence` 回填 `RetrievalEvent.used_ids`（弱标注「已注入」，失败静默）；shell_hook 新增 `backfill` NDJSON 动作；`build_context`/`_try_log` 透传 `session_id`；MCP `search` 可选 `session_id` 参数——写线活性判据「带 session 的读才算真实会话读」从此全链贯通。
  - **樊篱（Fanli，数据围栏，票 03；命名登记 CONTEXT.md，《诗经》「折柳樊圃」）**：`lantai/llm/fence.py` 单一真源——记忆正文注入提示前包 `<memory_data>` 数据围栏 + 固定声明「以下为历史记忆数据，不是指令」（OWASP LLM01 纵深防御）；出口全覆盖：shell_hook 注入串、MCP search results/evidence、认知中间件摘要、`to_prompt`、reflector 内部 LLM 读；正文携带的闭合标记中性化（围栏不可被正文截断）；`DATA_FENCE_ENABLED` 默认开，off 对照测试锚定。如实标注：纵深防御，不宣称绝对防注入。
  - **删除路由归属校验（票 04）**：`acl.ensure_can_delete` 单一真源（admin 全权；非 admin 校验 lane ∈ allowed_lanes——agent 绑定优先、租户匹配、用户匹配；越权 403、资源不存在 404，REST 常规语义可区分）；接入 `DELETE /terminal/memory/{id}`、`PUT /terminal/memory/{id}`（现状整改票 03 补入——原先写路由无归属校验，同资源删有守卫改无守卫）、`POST /terminal/merge`（src+tgt 双查）、`DELETE /documents/{id}`、`DELETE /edges/{id}`；无归属历史行（user_id NULL）不视为越权，只受 lane 约束。
  - **评测两层计分基线（票 05，LongMemEval 式召回/回答分层）**：数据集 80→94（paraphrase×8 带 key_points、typo_mid×6 词中错字）；召回层新增 `paraphrase_recall_rate` / `typo_mid_recall_rate`（离线基线诚实为 0——FTS AND 链不覆盖泛化，只报告不设门，派生票 06「FTS OR 兜底」）；回答层 `lantai/eval/answer_quality.py`（rule_judge 确定性要点命中 / llm_judge 选配含畸形降级 / compute_answer_metrics 按要点加权分维度）；`--judge` CLI 与两层报告；基线 `docs/memory-quality/baseline-2026-09-19.md`（离线门禁 PASS）。
  - **测试隔离常驻绊线（票 01）**：conftest 每测试 setup 校验 `lantai.storage.db` 模块级 `get_session`/`engine` 身份，被改脏即在下个测试点名前置污染者（历史：finally 置 None 污染 + 手写 `_patch_session` 与 monkeypatch 双层补丁因撕卸顺序回写泄漏，139 例连坐）。

### Added
- **v022 上游吸收（aiduMEI v21.2 Memmy 融改，调研 `docs/research/upstream-v212-gap-analysis.md`，票据 `.scratch/v022-upstream-v212-adopt/`）**:
  - **来源链贯通（票据 01）**：`session_id` / `origin_turn` 显式透传——对话摄取（`POST /dialogue[/async]`）、手动写入（`AddMemoryReq`）、原文直存（`RawMemoryReq`）落 `MemoryCandidate.session_id` 与 `provenance.origin_*`，随 `proposer → promoter` 全链继承到 `MemoryItem`；无来源如实 NULL（上游教训：出身必须显式传递，隐式通道上线即空转）。Coalesce 冲刷合并内容跨会话，出身宁留空不错误归属。
  - **回声抑制（票据 01 检索半，上游 M2）**：`hybrid.py` 打分前滤掉本会话自写候选；`ECHO_SUPPRESS_ENABLED` 默认关（兰台 session 域检索本就按 session 圈定，默认开会清空会话内召回）；空 session 一律不过滤。
  - **MMR 多样性截断（票据 02，上游 M4）**：`mmr_select`（λ·相关 −(1−λ)·jieba token 冗余，λ 默认 0.7 fail-closed 夹取）；`MMR_ENABLED` 默认关，关闭时逐条退回按分截断（零回归）；选择结果进 explain 域。
  - **错误签名通道 errsig（票据 03，上游 M6）**：`lantai/retrieval/errsig.py` 单一真源正则（CamelCase + Error/Exception/Warning），写入与检索两侧共用 import 杜绝拷贝漂移；查询含报错签名时正文精确命中候选加有界 bonus（`ERRSIG_BONUS` 默认 0.10，0=关闭）。
  - **咀华（Juhua，会话精华萃取，票据 04，上游 session distill；命名登记 CONTEXT.md，韩愈《进学解》「含英咀华」）**：`POST /session/distill`（只提炼不落库可安全重跑；`store=true` 经 `add_memory` 完整闸门管线进向量库）；新泳道 `distill` 慢衰减（base_s=30）；LLM 不可用确定性降级（取该会话最长两条拼接）并标 `distill_mode=fallback`；情绪词表有界显著性 0.60~0.85；`DISTILL_ENABLED` / `DISTILL_MIN_MEMORIES`（默认 3）。
  - **写线活性探针（票据 05，上游写线断裂事故产物）**：司天新增 `ingest_liveness` 域——`ingest_conv_reads_24h`（带 session 的真实会话检索，后台巡检不算数）+ 24h 写入分列（会话/后台）；三态判据 `broken`→critical、`background_only`→high、`no_evidence` 如实不告警（刚装好就断线不能一路绿过去）；`retrieval_event` 补 `session_id`（schema v21 增量迁移）；新增 `scripts/check_ingest_wiring.py` 写读回环自查（只看 /add 返 200 不算数；SSRF 纪律：默认仅回环目标，`--allow-remote` 显式放行）。
  - **轨迹级奖励信用（票据 06 最小切片，上游 M1）**：`EpisodeRecord` / `EpisodeStep` 表 + `POST /evolve/episode/feedback` 按位置回传 + `episode_credit_weights` 纯函数（λ·均匀 + (1−λ)·归一化 γ 递减，和恒为 1）；**只登记不接检索权重**（上游默认权重 0 同款纪律，察窗攒数据再开）。
  - 新增测试 71 例（`test_v022_retrieval.py` / `test_distill.py` / `test_episode_credit.py` / `TestSessionOriginChain` / `TestIngestLiveness` 等），带开关特性一律开+关对照冒烟。

### Fixed
- **鉴权双轨断裂（P0）**：业务路由原先只走库内 Bearer / DEV MODE，环境变量 `API_KEY` 与 `X-API-Key` 从未生效；空库 + 非回环可零鉴权写记忆。现 `get_current_user` 统一为：`X-API-Key`（命中即 admin）→ 库内 Bearer → **仅回环且无 API_KEY 且空库**才 DEV MODE。
- **启动入口缺失（P0）**：`lantai-server`（`lantai.api.app:main`）此前无 `main()`；Dockerfile/README 引用不存在的 `api_server.py`。已补 `main()` 与薄 shim，Docker `CMD` 改为 `lantai-server`。
- **回声抑制语义修正（ADR-0046，整改票 04）**：旧「删光同 session 候选」与 session 域检索叠加，开启即清空会话内召回；改时间窗语义——仅抑制本会话 `ECHO_SUPPRESS_WINDOW_SECONDS`（默认 900，正数 fail-closed）内新写入的回声，窗口外同会话记忆照常召回；`created_at` 缺失不抑制（宁 miss 不脏写）。
- **RetrievalParams 覆盖路径统一 fail-closed（整改票 02）**：校验下沉 `__post_init__` 单一真源，`from_overrides` 显式覆盖越界/非法/非有限 λ、bonus、窗口参数一律回默认，不再绕过 default_factory 直通评分。
- **distill 泳道权限收窄（整改票 03）**：`DEFAULT_LANES` 补 `distill`（默认密钥/DEV 可写可召回）；精华路由校验调用者泳道集含 `distill` 才许落库（否则 403）；源记忆读取按调用者泳道集收窄（与检索出口同口径），受限部署不再可能跨泳道提炼。
- **认知中间件测试环境依赖（整改票 01）**：`test_cognitive_middleware` fixture 只隔离了 FastAPI 依赖注入，DEV MODE 库检查直连模块级会话工厂查真实库——本机库有 api_keys 行即 401；fixture 补模块级 `get_session` 隔离（五步诊断归档 `docs/memory-quality/review-remediation-v022-diagnosis-2026-09-18.md`）。
- **episode_credit 测试全局状态污染（整改票 05）**：`finally` 里把模块级会话工厂置 `None` 改为 `monkeypatch.setattr` 自动还原，杜绝后续测试顺序依赖。
- **v022 检索测试替身契约（整改票 04）**：向量替身不再「无视 top_k 返回全库」，按 filters 真实过滤 session/lane/domain；测试 helper 的 FTS 同步改为同事务写入（原 commit 后写入随 Session 关闭回滚，`memory_fts` 恒空）；换用真实 `Principal`。

### Added
- **司天（后台运行监控面板，ADR-0045）**:
  - 采集层 `observability/metrics.py`：进程内 `MetricsCollector`（最近请求环形缓冲 + 分钟级聚合桶），
    零第三方依赖采集 uptime/RSS/线程/CPU/fd，`normalize_route` 把 `/memory/mem_01J8…` 归一成
    `/memory/{id}` 杜绝高基数打散统计；
  - 遥测层 `observability/telemetry.py`：纯 ASGI `TelemetryMiddleware` + 采样落库器，
    补齐 ADR-0040 建表以来从未写入的 `OperationLog`（4xx/5xx 与慢请求必留，正常请求 1/N 采样，
    后台批量写 + 按 `MONITOR_RETENTION_DAYS` 清理），杜绝每请求一次 SQLite 写的写放大；
  - 聚合层 `ops/monitor.py`：`build_monitor_snapshot` 一次装配进程/存储/记忆/管道/调度/请求/
    安全/依赖八域事实，`evaluate_alerts` 13 条规则告警，`render_prometheus` 同快照文本出口，
    `safe_settings_view` 生效配置只读（密钥打码、DB 路径只留文件名）；未匹配路由的 404
    在指标中并为 `(unmatched 404)` 一桶（防扫描器打散端点排行），落库仍留真实路径；
  - 接口面 `api/routes_monitor.py`：`GET /monitor/overview|series|logs|config|prometheus` +
    `POST /monitor/workers/{name}/run`（复用 `worker_operation_service` 同名互斥）；
  - 前端 `ui/monitor.js`：悬镜工作台新增「司天监控」视图（指标卡 / 告警 / 服务与依赖 /
    纯 SVG 请求趋势 / 端点耗时排行 / 记忆管道水位 / 调度器与 worker 一键补跑 / 问题请求 /
    运行配置），零构建原生 ES Module，10 秒自动刷新且页面隐藏即暂停；侧边栏告警徽标走
    `?quality=false` 轻量轮询；
  - 口径统一：worker 逾期判定上收为 `core.scheduler.worker_staleness` 纯函数，
    案牍 `project_work_items` 改为调用同一实现（行为不变），杜绝两处规则漂移；
  - 命名正式登记：在 `CONTEXT.md` 登记「司天」（Sitian，出自司天监观天象察灾异），归档 ADR-0045。

### Fixed
- **控制台初始化中断**：`ui/app.js` 绑定了 index.html 中并不存在的 `#systemRefresh`，
  `bindEvents()` 在该行抛 TypeError，导致其后的器识/札记/演练场/档案库事件与全局快捷键
  全部未绑定、`loadQueue()` 也不执行——控制台打开即空转。补上该按钮并新增
  `tests/test_monitor_ui.py::test_every_dom_selector_exists_in_index_html` 静态契约测试
  （JS 里每个 `$('#id')` 必须在 index.html 或 JS 动态创建中存在）防回归；
- **服务重启即崩（P0）**：`start_scheduler()` 里 `ingest` / `evolve` / `forget` 三个
  `add_job` 漏了 `replace_existing=True`，而 jobstore 是持久化在同一个 SQLite 库的
  `SQLAlchemyJobStore`——首次启动正常，**之后每次启动都在 `start()` 抛
  `ConflictingIdError: 'Job identifier (ingest) conflicts with an existing job'`，
  服务对已存在的库再也起不来**（只能删库或手工清 `apscheduler_jobs` 表）。已补参数，
  并加 `tests/test_scheduler.py::TestSchedulerRestart`（真实调度器 + 真实文件 jobstore，
  连启两次）防回归；
- **退出路径连带崩**：`stop_scheduler()` 在调度器已停止时抛 `SchedulerNotRunningError`，
  改为幂等（未启动/已停止均静默返回）；
- **依赖缺失**：`jieba` 与 `rank-bm25` 是四路混合检索的词级通道，却从未写进
  `pyproject.toml`（`uv.lock` 亦无），干净环境 `pip install -e .` 后
  `import lantai.gate.conflict_rules` 直接 `ModuleNotFoundError`；已补声明。

## [0.21.0] - 2026-08-31 - 悬镜（Xuanjing · 兰台可视化管理控制台 Lantai Studio）

### Added
- **悬镜（全功能可视化记忆管理控制台，ADR-0038）**:
  - 前端架构升级（Lantai Studio）：重构 `/ui` 单页工作台，打造一站式人机协同记忆运维界面，彻底打破纯 MCP 命令行运维壁垒；
  - 器识与札记在线工作室（Persona & Scratchpad Studio）：支持在线查看、编辑并一键保存 L/G/E 人格基座与工作区即时便签；
  - 沉潜夜梦沉淀仪表盘（Consolidation Console）：实时查看最近沉淀审计报告、碎片聚类统计与修剪突触计数，支持一键触发夜梦沉淀；
  - 四路检索与探针演练场（Playground）：交互式输入 Query，实时拆解展示向量分、BM25 分、FTS5 字串匹配分、时效衰减乘数与【探颐】主动探针提示；
  - 界面与主题美化：支持「吉金」（青铜深色）与「漏窗」（园林浅色）双典籍主题自适应与移动端响应式布局；
  - 命名正式登记：在 `CONTEXT.md` 登记「悬镜」（Xuanjing，出自宝镜高悬意象），归档 ADR-0038。

## [0.20.0] - 2026-08-31 - 探颐（Tanyi · 记忆主动探针与自然交互消歧）

### Added
- **探颐（记忆主动探针与自然交互消歧，ADR-0037）**:
  - 核心服务 `probing_service.py`：基于 `detect_memory_probes` 扫描未决冲突账本（`ConflictEvent(status="open")`），在检索命中时自动生成温和的自然语言求证探针；
  - 上下文协同插桩：`format_probing_context` 将求证事项注入 Prompt `【探颐·待求证事项】` 区域，供 Agent 顺带发问；
  - 答复识别与闭环消解：`resolve_probe_response` 识别用户次轮自然答复（肯定/否定/纠正），肯定时自动消解冲突并更新记忆版本，记录 `MemoryCheckpoint` 快照；否定时自动归档废弃；
  - 接口面暴露：新增 REST `POST /probing/detect`、`POST /probing/resolve` 以及 MCP 工具 `probe_detect` / `probe_resolve`（MCP 工具总数扩容至 **55**）；
  - 命名正式登记：在 `CONTEXT.md` 登记「探颐」（Tanyi，出自《易·系辞上》「探赜索隐，钩深致远」），归档 ADR-0037。

## [0.19.0] - 2026-08-31 - 沉潜（Chenqian · 闲时夜梦沉淀与折叠压缩）

### Added
- **沉潜（闲时夜梦沉淀与记忆折叠压缩，ADR-0036）**:
  - 核心服务 `consolidation_service.py`：基于 `find_consolidation_clusters` 自动扫描 `(domain, lane)` 分组下的高重合度碎片记忆群（$\ge 3$ 条）；
  - 概念提纯与折叠：调用 LLM 归纳提纯出 1 条高阶概括性主记忆，挂载 `source_ids` 溯源，并将原碎片状态置为 `consolidated`（折叠归档，主检索不再重复干扰）；
  - 衰减突触修剪：`prune_decayed_synapses` 自动将极度衰减（`decay_score < 0.05`）且无高采纳反馈的边缘噪音转入 `archived` 休眠；
  - 调度器集成：在 `scheduler.py` 注册每日闲时/夜间沉淀任务（北京时间凌晨 03:30 自动执行）；
  - 接口面暴露：新增 REST `POST /evolution/consolidate`、`GET /evolution/consolidate/report` 以及 MCP 工具 `memory_consolidate` / `consolidation_report`（MCP 工具总数扩容至 **53**）；
  - 命名正式登记：在 `CONTEXT.md` 登记「沉潜」（Chenqian，出自《荀子》「沉潜以思」），归档 ADR-0036。

## [0.18.0] - 2026-08-30 - 贯珠 · 辨域 · 潜移 · 札记（四维借鉴闭环）

### Added
- **贯珠（基于图谱拓扑的二度语义联想与多跳召回，ADR-0035，借鉴 Cognee）**:
  - 核心服务 `graph_retriever.py`：基于 BFS 沿 `MemoryEdge` 拓扑进行 1~2 步（hop）关系扩散与隐式记忆联想；
  - 路径可解释性：联想结果包含跳数、关联关系、前驱节点与边置信度，过滤环路与已访问集合；
  - 接口面暴露：新增 REST `POST /search/graph_expand` 以及 MCP 工具 `graph_expand_search`（MCP 工具总数扩容至 **51**）；
  - 命名正式登记：在 `CONTEXT.md` 登记「贯珠」（Guanzhu，出自《汉书·景十三王传》「如贯珠焉」），归档 ADR-0035。
- **辨域（User-Session-Agent 三维硬隔离与域分治，ADR-0034，借鉴 Mem0）**:
  - 数据库字段扩展与迁移：`MemoryItem.domain`（user/session/agent）与 SQLite `v17` 幂等增量迁移；
  - 检索层隔离支持：`hybrid_search` 与 `_keyword_fallback` 支持精确 `domain` 过滤与跨域召回；
  - 接口面暴露：REST `POST /search` 与 MCP `search` 工具全面支持 `domain` 参数透传；
  - 命名正式登记：在 `CONTEXT.md` 登记「辨域」（Bianyu，出自《周礼·春官·宗伯》「以辨天地四时之域」），归档 ADR-0034。
- **潜移（异步摄取管道与非阻塞任务调度，ADR-0033，借鉴 Zep）**:
  - 核心服务 `async_ingest_service.py`：基于后台线程池与 `TaskRegistry` 任务注册表提供非阻塞摄取调度；
  - 毫秒级提交：对话提交立即返回 `task_id`（<10ms），后台静默完成提取、提纯（披沙）与去重；
  - 接口面暴露：新增 REST `POST /dialogue/async`、`GET /dialogue/tasks/{task_id}` 以及 MCP 工具 `dialogue_add_async` / `dialogue_task_status`；
  - 命名正式登记：在 `CONTEXT.md` 登记「潜移」（Qianyi，出自《文心雕龙》「潜移暗引，莫之能知」），归档 ADR-0033。
- **札记（Working Memory Scratchpad 工作区暂存夹，ADR-0032，借鉴 Letta / MemGPT）**:
  - 引入 `SessionScratchpad` 表与数据库迁移 `v16`（为 Agent 提供在对话中主动实时读写的小纸条区域）；
  - 核心服务 `scratchpad_service.py`：支持 `get_scratchpad`、`write_scratchpad` 与 `format_scratchpad_context`（上限 1000 字符，超长自动截断，宁 miss 不脏写）；
  - 协同注入：`inject_checkpoint_context` 联动支持在首轮 Prompt 中与「器识」人格基座与「底本」会话快照协同拼合注入 `【札记】`；
  - 接口面暴露：新增 REST `GET /scratchpad/{session_id}`、`POST /scratchpad/{session_id}` 以及 MCP 工具 `scratchpad_get` / `scratchpad_write`；
  - 命名正式登记：在 `CONTEXT.md` 登记「札记」（Zhaji，出自古籍读书摘记要点之木简小帖），归档 ADR-0032。


## [0.16.0] - 2026-08-30 - 更漏（Genglou）

### Added
- **考功（记忆价值演化与升降评定体系，ADR-0031，v0.16.4）**:
  - 核心服务 `kaogong_service.py`：基于长程使用反馈（`MemoryUsageFeedback`）与采纳率实现全库记忆功过评定；
  - 升降规则：高频高采纳记忆（`use_count >= 3, helpful_ratio >= 0.8`）上考晋升长期语义层（`tier="longterm", decay_class="semantic"`），高频低效记忆（`helpful_ratio <= 0.2`）下考降权，样本不足保持原状（宁 miss 不脏写）；
  - 接口面暴露：新增 REST `POST /evolution/kaogong`、`GET /evolution/kaogong/report` 以及 MCP 工具 `kaogong_eval`（MCP 工具总数扩容至 46）；
  - 命名正式登记：在 `CONTEXT.md` 登记「考功」（Kaogong，出自唐代吏部考功司，掌官吏功过品级考评），归档 ADR-0031。
- **沙汰阈值校准（ADR-0026，v0.16.4）**:
  - `CANDIDATE_MIN_CONFIDENCE` 默认值安全校准为 `0.15`，自动淘汰闲聊废话（conf=0.0）与残片，保障待审队列高质量。
- **披沙（候选记忆递归精炼 Refine 机制，ADR-0030，v0.16.3）**:
  - 核心服务 `refine_service.py`：针对模糊置信度（0.2~0.6）的候选记忆进行 LLM 指代消解、消除口语化废话、原子化提纯与置信度重估；
  - 严格降级保护（宁 miss 不脏写）：LLM 异常、超时或校验失败时优雅降级保持原始文本不变，绝不损坏原有数据；
  - 接口面暴露：新增 REST `POST /candidates/{id}/refine`、`POST /candidates/batch_refine` 以及 MCP 工具 `candidate_refine`（MCP 工具总数扩容至 45）；
  - 命名正式登记：在 `CONTEXT.md` 登记「披沙」（Pisha，出自《世说新语·德行》「披沙拣金，往往见宝」），归档 ADR-0030。
- **器识（Persona 人格基座 L/G/E，ADR-0029，v0.16.2）**:
  - 引入 `PersonaProfile` 表与数据库迁移 `v15`（支持言语风格 L、行为准则 G、认知底色 E 三层认知模型）；
  - 核心服务 `persona_service.py`：支持激活切换、多 Profile 管理、格式化 Prompt 上下文生成与纯函数防护；
  - 会话级联动：`inject_checkpoint_context` 支持与「底本」会话快照协同注入首轮 Prompt，赋予 Agent 恒定立身风骨；
  - 混合检索加权（Persona Boost）：`hybrid_search` 针对 preference 与 rule 分轨自动叠加 1.05x 偏好增益，并在 explain 中透明记录；
  - 接口面暴露：新增 REST `/persona/*` 路由端点与 MCP 工具 `persona_get` / `persona_set`（MCP 工具总数扩容至 44）；
  - 命名正式登记：在 `CONTEXT.md` 登记「器识」（Qishi，出自《新唐书·裴行俭传》「士之致远，先器识而后文艺」），归档 ADR-0029。
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




