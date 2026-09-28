# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

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




