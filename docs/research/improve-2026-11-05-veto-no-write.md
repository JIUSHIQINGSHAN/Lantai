# 兰台改进 2026-11-05：Hermes 否决的一轮不再写进记忆

> 本 PR 由 Maya（Sesame 的 AI 助手）自动巡检后生成，经 GitHub 小号 jiushiqingshand-dev 提交，未经人工逐行审阅，合并前请自行核对。

结论：Hermes 的插件拦下一轮对话（比如 DLP 拦下“我的银行卡密码是 864213”）之后，lantai-hook 照样把这句原话送进对话写通道，还把它写成底本的“在做”块，下个会话首轮会原样注入给模型，等于把拦下来的密码又递了回去。这次改成：这一轮被否决，就从会话缓冲里剔掉，不写对话通道、不写底本，之前正常的轮次照常落库。改动只在插件一个文件里，约 45 行。

这是 AI 记忆研究周报 2026-11-03（`docs/research/papers/memory-weekly-2026-11-03.md`）里的 P0-1，当时只写了建议，没改代码。

## 这轮巡检看了什么

- 上次巡检基线 `c6214c06` 之后，master 有 6 个新提交，全部是小号 PR 的合入：#24（`1d32b4ca`、`1f37cfc8`）、#25（`6ab46bd6`，合并提交 `3259994e`）、#23（`222258cb`，合并提交 `a6f9d4ac`）。两个合并提交都没有冲突改写，内容与 PR 分支一致。
- 当前 master `a6f9d4ac`：本地全量 2923 个通过、5 个跳过、0 个失败；GitHub 上这两次 push 的 CI 都是 success。
- 新提交里没有你自己写的产品代码，没有新的审计问题，所以这轮按“挑一处已有代码改进”来做。小号此前没有开着的 PR，不会和别的 PR 改同一处。

## 问题

### Hermes 这边的行为（已对照 Hermes 源码 main `1fcc57e6`，2026-11-05）

- 插件在 `pre_llm_call` 返回 `{"action": "block", "message": "..."}` 就能否决这一轮：不发任何推理请求，把 message 当回复显示给用户（`website/docs/user-guide/features/hooks.md:702`）。
- 被否决时，`agent/conversation_loop.py:1617` 把退出原因设成 `PRE_LLM_BLOCK_EXIT_REASON`，即 `agent/turn_context.py:808` 的 `"blocked_by_plugin_pre_llm_call"`。
- Hermes 自己的外部记忆同步和后台记忆复盘遇到否决都直接跳过（`agent/turn_finalizer.py` 的 `_mirror_turn_to_memory`，`if vetoed: return`），注释写的是“被否决的提示词从不离开宿主”。
- 但 `on_session_end` 每一轮结束都会照常触发，带着 `turn_id` 和 `turn_exit_reason`（`agent/turn_finalizer.py:887` 起；文档 `hooks.md:471`）。
- 所有插件的 `pre_llm_call` 都会被调用，再取第一个 block（`agent/turn_context.py` 的 `_collect_pre_llm_call_context`），所以 lantai-hook 在自己的 `pre_llm_call` 里不可能提前知道这一轮会不会被拦。

### 兰台这边（修改前，`hermes-plugin/lantai-hook/__init__.py`）

- `_on_pre_llm_call` 一进来就 `_buffer_turn(session_id, q)`，把原话放进 `_session_buffers`。
- `_on_session_end` 不看 `turn_exit_reason`：先用缓冲里的文本 `build_session_blocks` 写底本五段块（`_call_checkpoint_write`），再 `_flush_session` 把每一条送给 `ingest_dialogue`。
- 结果：被拦下的原话进了对话写通道（直通、提取候选或待审队列），还成了底本的 `cp_active_intent`。底本会在下一个会话首轮由 `_call_checkpoint` 注入，原话就这样进了模型上下文。

### 复现（修改前，当前 master）

用假 ctx 驱动插件，`_call_hook`、`_call_checkpoint`、`_call_dialogue`、`_call_checkpoint_write` 全部 mock：

1. `_on_pre_llm_call(user_message="我的银行卡密码是 864213，帮我记一下", session_id="s1", turn_id="t1")`
2. `_on_session_end(session_id="s1", turn_id="t1", completed=True, turn_exit_reason="blocked_by_plugin_pre_llm_call")`

输出：

- master：对话写通道收到 `['我的银行卡密码是 864213，帮我记一下']`；底本块 `[{'cp_active_intent': '我的银行卡密码是 864213，帮我记一下'}]`
- 本分支：对话写通道收到 `[]`；底本块 `[]`

## 改法

只改 `hermes-plugin/lantai-hook/__init__.py`：

- 新增常量 `_PRE_LLM_BLOCK_EXIT_REASON = "blocked_by_plugin_pre_llm_call"`，和 Hermes 的值一致。
- `_buffer_turn` 多一个可选参数 `turn_id`。宿主给了才在条目里记 `"turn_id"`，不给时条目形状和以前完全一样（`{"text", "turn"}`），已有测试和下游都不受影响。`_on_pre_llm_call` 把 kwargs 里的 `turn_id` 传进去。
- 新增 `_drop_vetoed_turn(session_id, turn_id)`：
  - 给了 `turn_id`：只剔除 `turn_id` 相同的条目。一条都对不上就不删（比如被否决的是空消息，本来就没进缓冲），绝不误删之前正常的轮次。
  - 没给 `turn_id`（旧版 Hermes）：剔除末条。否决和 `on_session_end` 在同一轮里先后发生，末条就是这一轮；末条如果带着别的 `turn_id`，说明不是这一轮，不删。
- `_on_session_end` 开头：`turn_exit_reason` 等于否决值时先调 `_drop_vetoed_turn`，再按原逻辑写底本、flush。缓冲里之前没 flush 的正常轮次照常写入。

没改的：

- 被中断的一轮（`interrupted_by_user` 等）照旧写入。它已经发给过模型，要不要写是另一个产品问题，不在这次范围里。
- 轮次序号 `turn` 不回退：被否决的一轮仍占一个序号，和现有“说了两遍就占两个序号”的口径一致，来源链上会出现一个空号。
- 插件版本号 `plugin.yaml` 的 1.1.0 没动，要不要升由你定。

## 验证

- 新增 `tests/test_plugin_veto_no_write.py`，10 个测试：否决的一轮不写对话、不写底本；正常一轮照常写；中断的一轮行为不变；之前没 flush 的轮次保留、只剔否决那轮；否决后下一轮正常写且不夹带原话；旧宿主不传 `turn_id` 时剔末条；`turn_id` 对不上时不误删；`_drop_vetoed_turn` 单元行为；不传 `turn_id` 时条目形状不变；常量与 Hermes 一致。
- 撤掉插件改动、只跑这个文件：10 个里 6 个失败（另外 4 个是“正常写入不变”类的护栏，修改前后都应该通过）。
- 插件相关测试 `test_plugin_veto_no_write.py`、`test_hermes_plugin.py`、`test_plugin_short_query_persona.py` 一起跑：55 个通过。
- 全量 pytest（Python 3.11，项目 .venv）：本分支 2933 个通过、5 个跳过、0 个失败；master 同环境 2923 个通过、5 个跳过、0 个失败，差的 10 个就是新增测试。
- `ruff check` 通过，`ruff format --check` 全仓 806 个文件已格式化。

## 你怎么验收

1. 跑 `pytest tests/test_plugin_veto_no_write.py tests/test_hermes_plugin.py -q`，应全部通过。
2. 想在真 Hermes 上看：写一个只在消息含“密码”时返回 `{"action": "block", "message": "已拦截"}` 的小插件，和 lantai-hook 一起装上，发一句“我的银行卡密码是 123456”，再发一句正常的话。看 `ingest_dialogue` 的写入和底本表：只应出现第二句。

## 还剩的问题（只写建议，没改）

- **P1，检索日志仍记下被否决那一轮的原话**：lantai-hook 的 `pre_llm_call` 照常调 serve 检索，`lantai/observability/retrieval_log.py` 第 96 行把 `query_text=query` 原样写进检索事件表。插件在这一刻不知道会不会被拦，只能事后补救：在 `_on_pre_llm_call` 记下本轮的 `event_id`，否决时让 serve 删掉或抹掉这条事件的 `query_text`。要给 serve 加一个小接口，约半天。
- **P1，被否决那一轮仍记了注入回执**：同一处 `_call_backfill` 会刷新被检索到的记忆的“最后使用时间”并累加 `injection_count`，可模型其实没看到它们。改法是把回执挪到 `on_session_end`，只在没被否决时补发，约半天。这会改变回执的时机，和 #23 的 `injection_count` 口径相关，建议单独一个 PR。
- **更正周报 P0-1 的第 3 种改法**：周报说“把缓冲挪到 `post_llm_call`，它只在一轮正常结束时触发”。对照源码，这不准：`agent/turn_finalizer.py:756` 只要 `final_response` 非空且没被中断就会触发 `post_llm_call`（第 495 行），而被否决的一轮 `final_response` 就是拦截提示，所以 `post_llm_call` 一样会触发，`user_message` 里仍是原话。以后就算改用 `post_llm_call`（比如为了拿到回复给周报 P1-3 用），也得保留这次加的否决判断。

## 没验证的

- 没有用真的 Hermes 进程跑过端到端，只照着 Hermes 源码（main `1fcc57e6`）和官方 hooks 文档的调用参数做了模拟。
- 没在 Windows 上跑。
- LongMemEval 没跑，VM 上没有模型密钥；这次改动不碰检索，预计不影响。
