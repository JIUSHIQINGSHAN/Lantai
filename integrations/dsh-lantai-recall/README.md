# 兰台召回研究草案 / Lantai recall research draft

**自动召回暂停启用。** 此包保留标识与显示资源，默认无操作。配置 `enabled: true` 会在访问凭据、工作目录、事件或网络之前报错。不要安装此草案来启用自动召回。

这只影响本目录的 `lantai-recall`。独立的 [MCP 逐调用归因适配器](../dsh-lantai/README.md) 和显式的 `lantai-mcp-bridge` 调用保持各自契约，本次没有修改 DSH profile 或部署配置。

## 原因

已核对 DSH `0.2.2-rc.3` 的 inbox 与 waterfall 实现。首步消息可包含 steer 与注入内容，`source.kind: user` 不足以证明是所有者的新排队输入；外层监听器还可在内层返回后否决。同一凭据也不按调用工作区隔离服务端结果。原型无法仅靠这几个字段保证最终准入、输入来源和结果范围。

通用 `/search` 可按服务端策略保存查询原文及会话标记；返回内容进入 DSH 消息后也可能随会话保存、同步和送至模型。暂停或取消不能撤回已发送的数据。详见 [ADR-0090](../../docs/adr/0090-mainline-workspace-safety-contracts.md)。

## 后续研究条件

重新启用前须具备可信宿主提供的最终准入、实际 `next-turn` 输入归属、明确的后端数据范围与留存规则。使用真实 Cordis/AgentLoop/Session 和隔离后端覆盖非 owner、steer、混合注入、外层否决、取消、目录变更和回放；不能用最小监听器 stub 作为这些保证的证据。

当前只验收停用边界：`node --test test/recall.test.js`。通过表示默认无副作用、显式启用先拒绝，不代表自动召回功能已经验收。
