# 兰台周报 2026-11-15：发展方向与代码改进建议

> 本报告由 Maya（Sesame 的 AI 助手）每周自动生成，只含建议，不改产品代码。基线：origin/master `fc4fb2c8`（2026-11-14 13:50），Hermes Agent main（2026-11-15）与 v0.23.1。

## 一句话结论

**这周最值得做的一件事：让 Hermes 插件按 Hermes 真实的调用节奏走。** Hermes 的 `on_session_end` 是每一轮都触发的（官方文档原话："`on_session_end` is turn/run scoped"），可 `lantai-hook` 当成整场会话结束来用：每轮都把轮次计数清零、每轮都重写一次底本。结果是两件事：对话写通道收到的轮次号永远是 1（来源链里的 `origin_turn` 全废），底本只剩最后一句。我用真实的 `checkpoint_service` 跑了一场四轮会话，前三轮是“写发布说明 / 决定先修红测再出镜像 / 下一步合 #53 #54”，最后一句是“好的谢谢，我去吃饭了”，新会话读到的底本只有一段：“在做：好的，谢谢，今天就先到这里吧，我去吃饭了，明天再继续弄”，决策和下一步都没了。约 1 天。

## 一、兰台这一周（11-08 10:00 至 11-15）

- master 共 286 个提交（非合并 271 个）：fix 102、test 86、docs 68、style 8、refactor 2、chore 2，**没有 feat**。按天：11-08 75 个、11-09 111 个、11-10 27 个、11-11 26 个、11-13 30 个、11-14 2 个。
- 标题里带“红测”的提交 69 个（先写失败测试再修的流程）。上周建议给复验设停止条件，这周标题带“零新发现 / 零增量 / 逐字节 / 复验”的提交是 0 个（上周 11-07 一天就有 18 个）。
- **v0.68.0 于 11-13 22:03 打了标签**，但前一分钟（22:02）`20fe07ba` 把票 83 的红测合进了 master。`ci.yml` 在标签上跑测试失败，构建镜像那一步被跳过，v0.68.0 没有 Docker 镜像（巡检 11-14 已报）。
- 小号 PR：#46、#47、#48 已合并；#51、#52、#53、#54 开着等你合并。

## 二、上周建议跟进（2026-11-08 那份）

| 编号 | 建议 | 状态 |
| --- | --- | --- |
| D1 | 把“复验”收进门禁，主线回到发版和真实使用 | 部分采纳：复验类提交从 18 个降到 0 个，v0.68.0 发了；但发版时 CI 是红的、没出镜像 |
| D2 | 按 Hermes 会话类型分级读写 | 部分采纳：第一步（定时任务/子智能体只读）已由巡检做成 [#51](https://github.com/JIUSHIQINGSHAN/Lantai/pull/51)，还没合并 |
| D3 | 用真实使用数据判断注入质量 | 没动 |
| C1（P0） | cron 和子智能体的轮次不写入兰台 | 同 D2，等 #51 合并 |
| C2（P1） | 检索事件里记下“向量降级” | 没动（本周最后一次提，见改进 3） |
| C3（P1） | `reextract.py --apply` 的两个问题 | 已采纳：[#48](https://github.com/JIUSHIQINGSHAN/Lantai/pull/48) 已合并 |

## 三、本周外部动态（只列跟兰台有关的）

- **Hermes Agent [v0.23.1](https://github.com/NousResearch/hermes-agent/releases/tag/v0.23.1)（2026-11-10）**：主修插件目录（`plugin-catalog.json` 超过 2 MiB 上限，客户端一直回落到自带的旧目录，上限改成 16 MiB）。发布说明里还列了新插件接口：对话级环境变量、插件 JSON-RPC、`llm.oneshot` 辅助任务、**跨压缩不变的 `conversation_id`**、网关的 `on_session_open`、`post_curator_run`。
- **Hermes 钩子文档写明了生命周期**（[hooks.md](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/hooks.md)、[observer-hooks.md](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/observer-hooks.md)）：`on_session_end` 在每一轮结束时触发；一次性的会话清理要用 `on_session_finalize` 和 `on_session_reset`；`pre_llm_call` 会带 `is_first_turn` 和 `conversation_history`；`conversation_id` 是“对话的第一个 session id，压缩换 session_id 时不变”，官方建议“按它存每场对话的状态”（目前只在工具类钩子上带）。
- **Hermes 插件目录**：[plugin-catalog](https://github.com/NousResearch/hermes-agent/tree/main/plugin-catalog) 里 `category: memory` 的条目有 128 个，没有兰台。本周新收了 hermes-eye-memory（11-11）。条目要求写死提交 sha、标明 `requires_hermes` 最低版本，按目录名安装时会强制检查。
- **MemOS [v2.0.36](https://github.com/MemTensor/MemOS/releases/tag/v2.0.36)（11-09）**：含“harden memory evolution and Hermes installation”，它也在做 Hermes 插件，可对照它的安装方式。Cognee v1.6.8（11-12）仅供了解。
- **论文**：本周记忆研究周报（[#52](https://github.com/JIUSHIQINGSHAN/Lantai/pull/52)）已经详写了 [MemoryDojo](https://arxiv.org/abs/2611.09721)（记忆投毒）和 [MAPE](https://arxiv.org/abs/2611.17068)（保留原始对话当证据），这里不重复。
- **模型和服务**：硅基流动等国内 embedding / rerank 本周有没有上新，未核实。

## 四、发展方向（3 条）

### 方向 1（P0）：插件按 Hermes 的三层生命周期重排——每轮、每个 session、每场对话

- **做什么**：把插件里的状态分成三层。每轮：注入、写对话（带正确轮次号）。每个 session：轮次计数、底本累积，在 `on_session_finalize` / `on_session_reset` 时收口。每场对话（跨压缩）：用 `parent_session_id`（以后可换成 `conversation_id`）把压缩前后的 session 连起来，不让压缩一次就重新注入一遍“上次会话”底本。
- **为什么适合兰台**：兰台的底本（ADR-0022）和来源链（v022 票 01 的 `origin_turn`）都假设“一场会话结束才收口一次”，而 Hermes 实际是每轮触发。你用 Hermes 越多轮，这两样越不准。
- **证据**：见上面 Hermes 文档原文；本地复现见改进 1。
- **和现有计划的关系**：路线图和开着的 PR 里都没有。#51 只管定时任务和子智能体，不重叠。
- **工作量**：约 1 到 2 天（第一步即改进 1）。
- **第一步**：`hermes-plugin/lantai-hook/__init__.py` 的 `_on_session_end` 不再清轮次计数；`plugin.yaml` 的 `provides_hooks` 加 `on_session_finalize`、`on_session_reset`。

### 方向 2（P1）：发版门禁看远端 CI，红测换个落点

- **做什么**：打标签之前确认这个提交在 GitHub 的 `tests.yml` 上是绿的；票子的红测不直接进 master，要么留在票子分支，要么先加 `xfail(strict=True)`。
- **为什么**：这周 69 个“红测”提交，v0.68.0 打标签前一分钟进了票 83 的红测，标签发出去了、镜像没出来。`ci.yml` 自己挡住了镜像，说明 CI 那一层是对的，缺的是打标签前的一步。
- **工作量**：约半天（见改进 2）。
- **第一步**：`scripts/release_check.py` 的 `--online` 加一项检查。

### 方向 3（P2）：把 lantai-hook 上到 Hermes 插件目录

- **做什么**：给 `NousResearch/hermes-agent` 的 `plugin-catalog/` 提一条兰台的条目（写死 sha、`requires_hermes`、`known_issues` 写明需要先跑兰台服务）。
- **为什么适合你自己**：换电脑或重装 Hermes 时可以直接 `hermes plugins install`，更新走 `hermes plugins update`；`requires_hermes` 能挡住不传 `platform` 字段的旧版 Hermes（#51 的隔离依赖这个字段）。插件目录里已有 128 个记忆插件，MemOS 也在做 Hermes 安装。
- **前提**：先做完方向 1，否则把轮次号和底本的问题带给更多人。插件目录要求人工审核合并，是否公开由你决定。
- **工作量**：约半天写条目和 README 安装说明，审核时间不由你控制。
- **第一步**：对照 `plugin-catalog/README.md` 的 14 条规则，先补插件 README 里的网络调用和需要的密钥说明（规则 13 要求写明）。

## 五、改进已有代码（3 条）

### 改进 1（P0，约 1 天）：轮次号永远是 1、底本只剩最后一句

- **位置**：`hermes-plugin/lantai-hook/__init__.py`
  - 第 577 行：`_on_session_end` 每次都 `_session_turns.pop(session_id)`，而它每轮都被调用，所以 `_buffer_turn`（第 503 行起，注释写的是“该会话内 1-based 单调轮次计数”）每轮都从 1 开始。
  - 第 664 到 666 行：每轮用当轮的那一条消息调 `build_session_blocks`（第 477 行，规则是“末条 user 消息”），再写底本。
  - `lantai/services/checkpoint_service.py` 第 152 行：同一个 session 的底本是先删后插，所以前几轮写下的“决策”“下一步”被后一轮整段冲掉。
- **复现（本地，未用真的 Hermes）**：按 Hermes 的顺序每轮调 `pre_llm_call` 再调 `on_session_end`，底本接真实的 `checkpoint_service`（内存 SQLite）。四轮消息依次是：“我在写兰台 v0.68.1 的发布说明……”、“决定 v0.68.1 先修 CI 红测再出镜像，就按这个来……”、“下一步把 #53 和 #54 合进去……”、“好的，谢谢，今天就先到这里吧，我去吃饭了，明天再继续弄”。结果：
  - 写给对话写通道的轮次号：`[1, 1, 1, 1]`
  - 新会话读到的底本：只有 `cp_active_intent` 一段，内容是最后那句“我去吃饭了”；“决策”“下一步”两段都不在。
- **怎么改**：
  1. `_on_session_end` 只 flush 缓冲，不清 `_session_turns`；在新注册的 `on_session_finalize` / `on_session_reset` 里再清（`on_session_reset` 带 `old_session_id` 时清旧的）。计数表要有上限，防长期运行膨胀（现有 `_CHECKPOINT_INJECTED_MAX` 的做法可照搬）。
  2. 底本按 session 累积：每个块保留“最近一次命中的那条”，没命中的块沿用上一轮的值，而不是丢掉。写底本前先合并，再整体写入。
  3. “在做”不要被闲聊覆盖：对话写通道已经会把闲聊判成 rejected（ADR-0026），可以复用同一个判定，或者至少跳过短于 `CHECKPOINT_MIN_CONTENT` 加一句告别语的消息。
- **验收**：上面的四轮复现改完后，轮次号是 `[1, 2, 3, 4]`；新会话的底本里“决策”“下一步”都在，“在做”是发布说明那句。另加一条测试：压缩换 session_id 后（带 `parent_session_id`），不再重新注入“上次会话”底本。
- **风险**：旧版 Hermes 可能不发 `on_session_finalize`，计数表靠上限兜底；要补“每轮都调 on_session_end”的测试，否则修完也锁不住。CLI 模式的 `scripts/shell_hook.py` 这次没查，可能有同类问题。

### 改进 2（P1，约半天）：`release_check --online` 查一下这个提交的 CI 结论

- **位置**：`scripts/release_check.py` 第 297 到 330 行附近的 `online` 分支，现在只检查远程是否已有同名 tag（第 1646 行的帮助文字）。
- **问题**：`release_check` 在本地跑全量测试，但 v0.68.0 是在红测合进去一分钟后打的标签，本地检查跑的那棵树和打标签的那棵树可能不是同一个。
- **怎么改**：`--online` 时用 `gh run list --workflow tests.yml --commit <HEAD sha> --json conclusion` 取结论，不是 `success` 就判 FAIL；没有 gh 或没联网按现有约定记 SKIP（SKIP 本来就不会换来 0）。
- **验收**：在一个 CI 红的提交上跑 `--online` 返回 1；在绿的提交上返回 0。
- **风险**：CI 还在跑时结论为空，要按 FAIL 处理并提示“等 CI 跑完再发”。

### 改进 3（P1，约 1 天，上周 C2 最后一次提）：检索事件里记下“向量降级”

- **位置**：`lantai/retrieval/hybrid.py` 第 555 行向量检索失败只打 warning，第 567 行 `"fallback": not vector_results` 只写在 trace 里，第 573 行转去 `_keyword_fallback`；`lantai/models/tables.py` 第 652 行起的 `RetrievalEvent` 没有对应字段。
- **为什么仍值得做**：方向 1 和 D3 都要拿真实使用数据判断注入质量，可现在分不清一轮召回差是检索本身差，还是 embedding 超时退成了只用关键词。仓库自己的 `docs/research/兰台性能基线-2026-11-07.md` 写的是“端到端延迟的支配项仍是一次真实 embedding ~2s”，这个数没在你的网络上重测过。
- **怎么改**：`RetrievalEvent` 加一个 `vector_fallback`（布尔）和 `fallback_reason`（超时 / 401 / 未配置 / 空库），加迁移；悬镜 stats 里出一个 24 小时降级率。
- **验收**：把 embedding 地址指到一个不通的端口，跑一次检索，事件里 `vector_fallback=true`；正常时为 false。

## 六、未核实

- 改进 1 是在本地按 Hermes 文档写的调用顺序模拟的，没有用真的 Hermes 跑；CLI 模式的 `shell_hook.py` 没查。
- `conversation_id` 目前只在 Hermes 的工具类钩子上带，`pre_llm_call` 和 `on_session_end` 有没有计划加上，未核实。
- 硅基流动等国内 embedding / rerank 本周的上新情况，未核实。
- 插件目录对“需要另跑一个本地服务”的插件会不会收，未核实。
