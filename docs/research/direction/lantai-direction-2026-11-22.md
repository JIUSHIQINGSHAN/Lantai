# 兰台每周发展方向与代码改进建议（2026-11-22 期）

> 由 Maya（Sesame 的 AI 助手）撰写，经 GitHub 小号 jiushiqingshand-dev 提交。本期排在 11 月 22 日，因额度暂停，实际在 11 月 25 日跑完；代码基线是 master `02fabb01`（2026-11-25 07:52），这个提交的 CI 是绿的。
>
> 验证环境：云端真 Hermes Agent v0.23.1 + 兰台插件，模型是本地假 OpenAI 服务（只验证链路，不评估回答质量）。没有在 Windows 上跑，没有用你本机的 Hermes 和生产库跑。

> 2026-11-27 收口补记（对照 master `4e066a81`）：下文保留 `02fabb01` 基线的历史观察，不表示这些问题仍全部存在。C1 的对话写入属主问题已由 #81（合并提交 `e3ac329b`）修复，写侧复用 `_hook_principal()`，显式配置属主时传给 `ingest_dialogue`；对应真实写入与检索冒烟见 `tests/test_hook_dialogue_owner.py`。这不表示底本读写、健康诊断、轮次或插件配置等延伸建议也已完成，更不涉及历史数据迁移。本 PR 仅收录方向报告，合并不代表批准后续实施。

## 一句话

**本周最该做的是把钩子的“写入属主”和“读取属主”对齐。** 只要按票 90 配了 `LANTAI_HOOK_USER_ID`，钩子就只读那个属主的记忆，可它从对话里写出去的东西仍然挂在 `default` 下：今天聊的事，往后它自己永远检索不到。

## 一、本周进展（11-15 以来）

- master 新增 124 个非合并提交，其中 15 个 `feat`：底本跨会话定本（ADR-0097）、守藏按来源信任分级入库（ADR-0096）、DSH 对话提取开关（ADR-0092）、校雠去重、拾遗分阶补救、mirasim-lantai 插件包等。
- 发了 v0.69.0（11-18）和 v0.69.1（11-19）。
- 小号这段时间合进去 13 个 PR：#51 #52 #53 #54 #55 #56 #58 #59 #63 #65 #68 #69 #71。现在小号没有开着的 PR；你自己开着 #79（注入帧日期标签）。
- 你在 #77 自己做的实机验收里，P0-4 写的是“检索零命中但不报错”。下面第一条就是这类问题在写入一侧的翻版。

## 二、上期建议跟进（2026-11-15 期，#55）

| 编号 | 建议 | 现状（以 `02fabb01` 为准） |
|---|---|---|
| D1 | 插件按 Hermes 三层生命周期重排 | 部分采纳：#51 合了，定时任务和子智能体不再写入；每轮/会话/对话三层还没拆开 |
| C1 | 轮次号恒为 1、底本只剩末句 | **没修**：`hermes-plugin/lantai-hook/__init__.py:648` 在 `_on_session_end` 里仍然 `_session_turns.pop(session_id)`，Hermes 每轮都触发这个回调，所以下一轮又从 1 开始 |
| C2 | `release_check --online` 查 HEAD 的 tests.yml 结论 | 没动：`scripts/release_check.py:320` 的 `--online` 仍只查远端有没有同名 tag |
| C3 | RetrievalEvent 记向量降级 | 已采纳（#48） |
| D2 | 发版门禁看远端 CI | 没动（同 C2） |
| D3 | lantai-hook 上 Hermes 插件目录 | 没动：Hermes v0.23.4 源码里搜不到 lantai |

C1 已经连续两期排在前面，这是最后一次提。

## 三、外部动态（只列和兰台有关的）

- **Hermes v0.23.3 / v0.23.4**（GitHub Releases）：v0.23.4 加了 `hermes plugins config`，插件在 `plugin.yaml` 里声明 `config_schema` 后，用户可以用命令行改插件配置，不用手改 `.env`。兰台的 `plugin.yaml` 现在没有 `config_schema`，所以 `LANTAI_HOOK_USER_ID`、超时这些设置只能靠 `.env`。
- **Hermes 的 `pre_llm_call` 带 `sender_id`**：v0.23.4 的 `agent/turn_context.py:926` 调这个钩子时，会把网关上的发送者 id 一起传进来。兰台插件现在完全不读它（`grep sender_id` 为空）。以后要做多用户，这是现成的身份来源；但按票 90 的原则，它只能作为显式映射的输入，不能直接拿来当属主。
- **graphiti v0.32.1、cognee v1.6.10**：本周都是修补版，没有和兰台直接相关的新机制。
- **硅基流动**：官方价格页（siliconflow.cn/pricing，11-25 读取）里 BAAI/bge-m3 和 bge-reranker-v2-m3 仍然免费，本周没有新的向量或重排模型。兰台默认配置不用改。

## 四、发展方向

### D1（P0）身份成为钩子的一等配置：读、写、底本用同一个属主

票 90 把“读谁的记忆”做成了显式配置，方向是对的，但只做了读这一半。建议把“钩子以谁的身份工作”收成一个函数，读检索、写对话、读写底本都从它取，再在 `health/deep` 里报出来。这也是以后接 Hermes `sender_id`、做多用户的前提。

### D2（P1）插件配置走 Hermes 自己的配置面

给 `plugin.yaml` 补 `config_schema`，把 `LANTAI_HOOK_USER_ID`、`LANTAI_HOOK_ROLE`、`SHELL_HOOK_TIMEOUT` 列进去。好处是配错能在 `hermes plugins config` 里看到，而不是像 #77 P0-4 那样静默零命中。要先在 v0.23.4 上确认插件读配置的接口，再动手；本期没有实测。

### D3（P1）发版门禁看远端 CI（延续上期 D2/C2）

v0.68.0 带着红 CI 发版的事还没有门禁兜底。

## 五、改进已有代码的建议

### C1（P0）钩子写入属主和读取属主不一致

**问题在哪**

- 读：`scripts/shell_hook.py:333` `_hook_principal()` 在 `LANTAI_HOOK_USER_ID` 非空时构造 Principal，检索只看这个属主。
- 写：同一个文件 `:641` 的对话通道调的是 `ingest_dialogue(text, session_id=..., turn=...)`，没传 `user_id`；`lantai/ingestion/dialogue.py:93` 的默认值是 `"default"`。插件模式和 CLI 模式都走这里。
- 代码注释自己写着：真实库 265/276 条记忆挂在 `api_key` 下。也就是说，只要你为了看见这些记忆配上 `LANTAI_HOOK_USER_ID=api_key`，从此钩子写进来的新记忆就全落在 `default`，它自己看不见。

**复现证据**

1. 进程内（临时库，同一句查询）：
   - `LANTAI_HOOK_USER_ID` 为空：只命中 `default` 下的那条；
   - `=api_key`、角色 user：只命中 `api_key` 下的那条；
   - `=api_key`、角色 admin：两条都命中，但 admin 等于放弃隔离，不是修法。
2. 真 Hermes v0.23.1 插件模式：库里预置两条记忆，一条属主 `api_key`（“检索预算定为八秒”），一条属主 `default`（“以前是五秒”）。问一句“兰台的检索预算 SHELL_HOOK_TIMEOUT 现在是几秒？”
   - 不配置：模型收到的是 `mem_owner_def`（五秒那条）；
   - 配 `LANTAI_HOOK_USER_ID=api_key`：模型收到的是 `mem_owner_api`（八秒那条）；
   - **两次跑完，这一轮写出去的候选和 ingest_event 都是 `default|1`**。配了 api_key 以后，这条候选将来晋升成记忆，同一个钩子也读不到。

脚本在 Maya 的工作区：`principal_probe.py`、`seed_owner.py`、`run_owner_compare.sh`，需要的话可以另提 PR 放进 `scripts/e2e/`。

**怎么改**

1. 在 `shell_hook.py` 加一个 `_hook_owner()`：`_hook_principal()` 非空时返回它的 `user_id`，否则返回 `"default"`。
2. `:641` 改成 `ingest_dialogue(text, user_id=_hook_owner(), session_id=..., turn=...)`；底本读写（`checkpoint_service` 的调用点）同样带上属主。
3. 不碰历史数据。另给一个只读诊断：`health/deep` 增加 `hook_owner` 和“该属主可见的活跃记忆数 / 全库活跃记忆数”，配错一眼能看到。
4. 历史上已经落在 `default` 下的对话记忆要不要迁到 `api_key`，**需要你决定**，建议另起一个带 `--dry-run` 的脚本，先备份再做。

**验收**

- 新测试：设 `LANTAI_HOOK_USER_ID=api_key`，跑一次对话通道，断言 `memorycandidate.user_id == "api_key"`；不设时仍是 `default`（老行为不变）。把修复撤掉，第一个测试应当失败。
- 真 Hermes：重跑上面的对比，第二次的候选应变成 `api_key|1`。
- 工作量：半天。

### C2（P0，延续，最后一次提）轮次号恒为 1

`hermes-plugin/lantai-hook/__init__.py:594` 每轮 `+1`，`:648` 在 `_on_session_end` 里清掉；Hermes 每轮都调 `on_session_end`，所以轮次永远是 1，底本只留末句。改法和验收见 #55 C1。11-16 巡检（#59 附带验证）在真 Hermes 上复现过：三轮会话写出的 3 条候选，轮次号全是 1。

### C3（P1）`release_check --online` 查远端 CI（延续上期 C2）

`scripts/release_check.py:320` 的 `--online` 分支加一步：用 `gh run list --commit <HEAD> --workflow tests.yml` 取结论，不是 `success` 就列为阻断项；没装 `gh` 或没网时给警告，不阻断。验收：拿一个 CI 红过的提交跑，应报阻断。

### C4（P2）`plugin.yaml` 补 `config_schema`

见 D2。先在 v0.23.4 上确认读取接口，再改 `plugin.yaml` 和插件里读配置的地方；`.env` 继续兼容。

## 六、没做的和不确定的

- 没在 Windows、你本机的 Hermes、生产库上跑；生产库里 `default` 下现在有多少条记忆，我看不到。
- 真 Hermes 用的是假模型，没评估回答质量。
- C4 只读了 Hermes 文档和源码，没实测。
- 本期只提文档，代码一行没改。
