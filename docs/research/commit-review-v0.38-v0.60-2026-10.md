兰台（Lantai）记忆仓库续审：v0.38 → v0.60
made with Sesame

  给读这份报告的 AI：本文自包含，只读它就能理解每个问题。每条问题都写了位置（文件:行号，行号以 v0.60.0 = ac30af91 为准）、引入提交、复现证据、修法和验收标准。审查只读，没有改仓库代码，没有推送。

0. 审查范围与方法

- 仓库：https://github.com/JIUSHIQINGSHAN/Lantai ，master。
- 上次评审终点：v0.38.0（206ece5f）。本次 git fetch 后 master 到 v0.60.0（ac30af91，2026-10-30 19:47 +0800）。
- 新增：90 个提交、22 个版本标签（v0.39–v0.60），时间集中在 2026-10-30 01:51 到 19:47（北京时间）这 18 小时内；其中 83 个提交由 Claude Code 共同署名。lantai/ 目录净增约 2.1 万行；新增 17 个 lantai/cognition/*.py 引擎；MCP 工具从 69 个涨到 94 个，默认仍全部暴露。
- 环境：Ubuntu VM，Python 3.13，未配 API_KEY/LLM 密钥。全量 pytest 用 pytest tests/ -q -o addopts=""。
- 复现方式：对每个新问题写了最小脚本（临时 LANTAI_HOME、FastAPI TestClient、直接调用服务函数），结果原样贴在每条的“证据”里。
- 编号：上次的问题沿用原编号（D-xx / R-xx / C-xx）；本次新问题用 N-xx（新提交引入）和 X-xx（扩展审查发现的存量问题）。

1. 最重要的结论

1. 主干仍是红的，而且更红了。v0.38 之后 GitHub Actions 只跑了一次（HEAD ac30af91），Tests 失败；v0.39–v0.59 这 21 个标签从没跑过 CI。本地全量：2420 passed / 48 failed / 2 errors / 5 skipped（v0.38 是 2258 / 33 / 2）。上次的 P0 一条都没修。
2. E2 时态评测“变绿”是假象（N-01）。E2 在 v0.60 通过，二分定位到 2a29e461（拾遗零召回补救）。关掉拾遗后 E2 仍是 31/37，同样 6 题返回空。拾遗只是用“忽略 as_of 的子串匹配”把空结果填上了，同一提交还打挂了 7 个隔离/时态测试（领域隔离、as_of 视图、同袍 agent 隔离、verbatim 默认不召回等）。
3. 删除不彻底，已删的隐私会被重新注入给 Hermes（X-01，P0）。终端删除和文档删除都不级联删 fact_atomic；完璧事实检索没有“来源记忆仍 active”的约束；_attach_wanbi_facts 在没有血统匹配时把事实挂到排第一的任意结果上，shell_hook.py:278 再把它注入 prompt。实测：删掉“我的银行卡密码是 864213”后，查询“我的银行卡密码”，这句话挂在一条“我喜欢乌龙茶”的结果上返回。
4. 只靠 Hermes 插件使用时，几乎所有记忆 60 天后会被自动归档（X-02，P0）。注入路径从不更新 last_used_at/use_count/helpful_count，而遗忘规则里 tier=="working" 且 helpful_count==0 超过 60 天就归档，不看重要度。实测：importance=0.95 的偏好、65 天前写入、衰减分 0.148，仍被归档。
5. 私密数据默认写进 Git 工作区（X-05）。上下文卸载全文写到 docs/memory-offload/、知识库写到 docs/memory-wiki/，两者都不在 .gitignore 里，且 docs/memory-wiki/overview.md、index.md 已被跟踪，一次 git commit -am 就会把记忆摘要推上公开仓库。

2. 上次 24 条问题与 P0 清单的核对结果

结论：v0.38→v0.60 的 90 个提交里，只有 2a29e461 碰过上次点名的热路径（hybrid.py）；shell_hook.py、hermes-plugin/、probing_service.py、temporal.py、forgetting.py、.github/workflows/ 都没改。

| 编号 | 问题（上次） | v0.60 状态 |
|---|---|---|
| 主干红灯 | Tests 自 v0.28 起连续失败，Docker 照推 | 未修，变差：失败 33→48；21 个标签未经 CI |
| D-01 | scripts/shell_hook.py:243-246 生产代码改写 hybrid 模块全局 | 未修；test_shell_hook ×3 仍只在全量顺序下失败 |
| D-06 | f74343e0 改 temporal.py 致 E2 37/37→31/37 | 未修，被掩盖（见 N-01） |
| D-13 | shared_scope NOT NULL 无 server default | 未修；test_genglou_migration ×2 仍 ERROR |
| R-01/C-16 | probe_resolve 否定子串匹配误改记忆 | 未修；probing_service.py:45-70 规则原样 |
| D-02 | 插件 15 字预过滤让画像在短消息不注入 | 未修；hermes-plugin/lantai-hook/init.py:459 原样 |
| D-03 | 0.5 阈值混用两种量纲 | 未修 |
| D-04 | hook 内两次网络调用挤 2 秒超时 | 未修；且拾遗在零召回时再加一次全表扫描（N-02） |
| D-05 | 画像未经 wrap_as_data、无字数上限 | 未修；写入侧还缺注入闸门（X-04） |
| C-03 | 插件残留 Path("D:/Projects/记忆") | 未修；init.py:62 |
| C-07/C-08 | LongMemEval / selfproof 未重跑 | 未重跑（本次也无密钥） |
| D-07/C-09 | 代号复用、ADR 索引错乱 | 未处理 |
| C-10/M1 | 探颐多套探测路径 | 未合并 |
| D-09 | 上次称 ENABLE_PROACTIVE_PROBING 未在已接线路径读取 | 上次结论有误，作废：v0.38 起 probing_service.py:97 就已读取该开关 |
| C-11/M2 | 尘封另写一套归档且未接线 | 未处理 |
| C-12/M3 | 格物 CausalEngine 未接线 | 反向演化：323d6d8c 又给 causal.py 加了 337 行，生产路径仍无调用方 |
| C-13/M4 | 三省与 reflector.health_scan 重叠 | 反向演化：a755062a 给 sanxing.py 加了 263 行，未合并 |
| D-08/M5 | CognitiveContext 字段膨胀 | 变差：cognition/context.py +436 行 |
| C-15/R-02 | MCP 69 个工具默认全暴露 | 变差：94 个，LANTAI_MCP_PROFILE 不设时仍全量 |
| D-14 | selfproof 文字与数据不一致 | 未改 |
| D-15 | plugin.yaml 名称与版本过时 | 未改 |
| D-16 | Hermes 三项改动零专门测试 | 未补 |
| 过程 | AI 大提交、正文空 | 延续：18 小时 22 个版本 |
| G1 | 镜像构建 needs: tests + 分支保护 | 未做：.github/workflows/*.yml 里没有 needs: |

3. 新提交引入的问题（N）

N-01【P0】拾遗零召回补救绕过所有检索过滤，掩盖 E2 回归并打挂 7 个隔离测试
- 引入：2a29e461 feat(shiyi): 核心主流水线零召回自适应拾遗补救闭环（v0.39 段）。
- 位置：lantai/retrieval/hybrid.py:683、:1151、:1242；lantai/cognition/shiyi.py:157、:103-116、:179-205。
- 现象：主流水线结果为空时，拾遗对所有 active 记忆做 Python 子串/字符重合匹配，只传 query/domain/limit，不传 as_of、as_of_recorded、time_from/time_to、lane、layer、verbatim 排除、shared_scope、agent 隔离。
- 修法：立即把 enable_shiyi 默认改为 False，或至少在传了 as_of/as_of_recorded/time_from/time_to/layer/lane、或 principal is None 时跳过拾遗。

N-02【P1】拾遗在热路径做全表扫描，零召回查询多 1 秒左右
- 位置：lantai/cognition/shiyi.py:157 起的循环，对每条 active 记忆做分词与字符匹配。
- 证据：20,000 条记忆实测：开拾遗 4.0 秒，关拾遗 3.1 秒。hook 预算仅 2 秒，直接超时。

N-03【P1】考功服务重构后，无身份调用会评估并降权所有人的记忆
- 引入：40112ced。位置：lantai/services/kaogong_service.py:39-44，lantai/cognition/kaogong.py:261-267。
- 证据：无身份调用评估了别人的记忆，导致 test_evolution_write_ownership 等 4 个测试失败。

N-04【P2】MCP 工具涨到 94 个且默认全量暴露；17 个新引擎只有拾遗进了生产路径
- 证据：新引擎里只有 shiyi 被生产代码引用；其余只能经 MCP/REST 触达。默认暴露 94 个工具挤占 AI 上下文。

N-05【P2】新增工具让工具数断言测试过期（test_sanxing_api_smoke 等）。

N-06【P2】仓库根目录被提交了评审补丁和两个零字节垃圾文件（`票据目录：。`、`遵循`）。
- 修法：git rm 这三个文件。

N-07【P1】发版流程：21 个标签未经 CI，HEAD 红着发布。

4. 扩展审查：存量问题（X）

X-01【P0】删除不级联原子事实，已删内容会被挂到无关结果上注入给 Hermes
- 位置：lantai/api/routes_terminal.py:361（不删 fact_atomic）；lantai/retrieval/hybrid.py:951-953（无血统事实挂到排第一的条目）；scripts/shell_hook.py:278（注入 prompt）。
- 证据：删掉“我的银行卡密码是 864213”，查询“银行卡密码”，这句话被挂在“我喜欢乌龙茶”上注入给 Hermes！
- 修法：统一 purge_memory 级联删除；事实检索强制要求来源记忆 active；删掉“无血统挂第一条”的兜底逻辑。

X-02【P0】hook 路径不记录使用，60 天“工作区 TTL”会把重要记忆批量归档
- 位置：lantai/memory/forgetting.py:100-115（working tier 且 helpful_count==0 满 60 天强制归档）。
- 根因：Hermes hook 注入从不更新 helpful_count。导致重要偏好（importance=0.95）60 天后全被静默归档。
- 修法：hook 异步回填使用记录；TTL 规则改为 use_count == 0 且豁免高重要度与偏好。

X-03【P1】终端合并与编辑接口绕过闸门，自合并会删掉记忆
- 位置：lantai/api/routes_terminal.py:522-567（source_id == target_id 时物理删除自身）。

X-04【P1】人格基座写入不过注入闸门，经 MCP 暴露，并每轮无围栏注入（persona_set 注入漏洞）。

X-05【P1】卸载全文和知识库默认写进仓库 docs/，不在 .gitignore，随时泄露隐私。

X-06【P1】密钥闸门默认关闭，且只覆盖对话摄取一条路径。

X-07【P2】备份脚本不读配置，从别的目录运行会“成功”地备份空内容。

X-08【P2】文档写 SQLite WAL，代码没开；多进程写同一个库。

X-09【P2】重排序默认把 OpenAI 密钥发给 SiliconFlow。

X-10【P3】README 与实际不符（写死测试通过数等）。

5. 更新后的 P0 行动清单

1. 关掉拾遗或加严过滤（N-01，0.5 天）
2. 修 D-06 时态透传，E2 ≥ 0.9（0.5–1 天）
3. 删除级联与事实挂载（X-01，1 天）
4. 停止 60 天批量误归档（X-02，1 天）
5. 私密目录移出仓库（X-05，0.5 天）
6. 历史 P0 项：D-01、D-13、R-01、N-03
7. G1 发版闸门（0.5 天）
