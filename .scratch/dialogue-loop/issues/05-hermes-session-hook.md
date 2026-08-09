# 05 - Hermes 会话结束钩子验证

Status: ready-for-agent
Type: research
Blocked by: (none)

## 目标

验证 Hermes 是否有会话结束事件可挂接"对话自动写入"，为 01 提供自动触发源。

## 范围

- 研究 Hermes 插件 API（已有 pre_llm_call Python 插件）是否支持 session 结束/空闲事件
- 备选：定时扫描 Hermes state.db 新消息（只读连接 + WAL，跨进程安全）
- 输出：结论 + 推荐实现（写回 spec 的触发源部分）

## 验收

1. 明确结论：插件事件存在 / 不存在 / 需折中
2. 备选方案（state.db 扫描）的 schema 与安全要点记录
3. 结论写入本 ticket 的 ## Answer，并回写 spec.md

## 相关文件

C:\Users\Asus\AppData\Local\hermes\（插件与 state.db 只读探查）、
docs/plans/v0.5-dialogue-loop.md、.scratch/dialogue-loop/spec.md
