# ADR-0051: 宿主适配矩阵——Shell Hook 契约泛化与降级档（host adapter matrix）

**日期**: 2026-09-26
**状态**: Accepted
**决策者**: 大哥
**来源**: roadmap-v2 P1-2（`docs/research/memory-landscape-2026-09/roadmap-v2.md:24`）；iter-13 D24（churn：以宿主矩阵 + 注入回执替代 MCP 工具扩容）；宿主可行性实证 `docs/research/memory-landscape-2026-09/followups/2026-10/02-host-hook-integrations.md`；设计规格 `docs/specs/2026-09-26-host-matrix-design.md`；实施票据 `.scratch/host-matrix/`

---

## 背景

兰台此前只接入**一个**宿主（Hermes 插件 + `scripts/shell_hook.py`）。宿主单点使「来源链 + 注入回执」（[ADR-0049](0049-receipt-chain.md)）的治理承诺只对单宿主可证明——roadmap 的 integration 分项停在 3.5（对照 agentmemory 4.5）；差距在宿主矩阵而非工具面（MCP 59 工具已足量）。且 shell hook 的 NDJSON 契约散落在代码注释中，未成一份可读的宿主接入协议。

本次把该契约泛化为**宿主适配层**。因其变更触及**对外契约**与**降级逻辑**（`docs/development-workflow.md` 阶段 3.1 明列须撰 ADR 的两类），故补此决策记录。

## 决策

### 1. 三层分离（协议归一化 / 宿主适配 / 宿主实现）

- **协议归一化层**（`lantai/integrations/host_protocol.py`）：解析、字段校验、渲染。**纯函数、无 IO、无子进程**；不含业务逻辑。
- **宿主适配层**（`lantai/integrations/host_adapters.py`）：把兰台自有响应帧翻译为宿主钩子要求的形状。**薄、无状态**；不含协议逻辑。
- **宿主实现**（`scripts/shell_hook.py`）：进程边界 + 分发到 handler + 超时包装。既有 handler（`build_context` 与全部 `_handle_*`）**逐字保留**。

**契约稳定支点**：`_handle_one(raw) -> dict` 的签名与返回**逐字节不变**，新层藏在其内部；行为等价由「既有测试断言零改动通过」证明。

### 2. 宿主矩阵 = Hermes + Claude Code + Codex CLI（命令钩子）

三者均为命令型钩子宿主，同构驱动同一协议；验收口径「≥3 宿主端到端冒烟」即以此三者为矩阵。**只做读路径注入**，不依赖各宿主的阻塞/拒绝语义（Codex 的阻塞语义官方 config-reference 未载，不作为设计前提）。

### 3. 响应形状与宿主标识（对外契约）

- Hermes 直通兰台自有形状（`{context, evidence, event_id}` 顶层）。
- Claude Code 与 Codex 走 `hookSpecificOutput.additionalContext` 通道（官方文档一手实证二者同形状）；兰台元数据（`event_id`/`request_id`/`evidence`）作**顶层兄弟字段**带出——回执（backfill）需要 `event_id`。依据 Claude Code 官方 hooks-guide：未知顶层键被静默忽略（仅记 debug 日志），**仅 `Stop` 事件严格校验**，而本协议只用 `UserPromptSubmit`/`SessionStart`，故安全。
- **宿主标识经环境变量 `LANTAI_HOST`**：未设置 = 直通；`claude-code`/`codex` = 包装。这是宿主的**必设项**（不设置则注入静默失效）——已写入协议文档。
- **写路径结果不翻译**：`backfill`/`dialogue`/`checkpoint*` 的控制面应答原样返回——包成 `additionalContext` 会抹掉 `receipt_status`。

### 4. Cursor 降级档（**不计入命令钩子矩阵**）

调研实证（2026-09-25 一手来源）Cursor **无命令型钩子入口**，只能静态规则注入（`.cursor/rules/*.mdc` + `AGENTS.md`），**无运行时检索、无回执**。

**决策**：Cursor 作**降级档**单独交付，并**如实声明不计入命令钩子矩阵**——把静态规则注入混入「≥3 宿主端到端冒烟」会让验收口径名不副实（命令钩子冒烟：真实子进程 + 协议帧 + 非空 context + 可回执；规则注入：静态文件存在性——两者不同构）。其降级档由安装脚本生成 `.mdc` 规则 + 追加 `AGENTS.md` 片段（跨宿主最低公分母）。

### 5. 安装出口的降级逻辑（安全）

`scripts/install_host_hooks.py`：**默认只打印**配置片段、**不写**宿主目录（宁 miss 不脏写）；`--write` 才落盘并打印落点；**落点已存在则拒绝覆盖**。生成的命令**跨平台**（不用 `VAR=value cmd` 的 POSIX 形式——win32 的 cmd/PowerShell 报「不是内部或外部命令」，实证 rc=1；路径用正斜杠以免 `-c` 源码串的转义陷阱）。

### 6. 命名（不起新名）

统一用描述性短语「宿主适配层 / 宿主矩阵」，扩写 `CONTEXT.md` 既有「Shell Hook」条。依据 [ADR-0013](0013-naming-system.md) 命名纪律（延续 [ADR-0050](0050-consolidation-audit-gate.md) 决策 8 的「不起新名」先例：动作/审批/验证语义邻域已密集占用，新造词易一物多意象）。

## 后果

- 治理承诺跨宿主可证明：三宿主各自跑通「注入 → 回执 → 故障隔离」，`event_id` 全链可回执（`receipt_status="acked"`）。
- 接新宿主成本降为「一个薄帧映射 + 一份配置」——协议核心与 handler 不动。
- **顺带修复两处真实缺陷**（实施中发现）：①非对象 JSON 帧原抛 `AttributeError`，`--serve` 常驻循环里一个畸形帧即打死进程，今静默降级；②CC/Codex 响应适配最初连回执应答也包成 `additionalContext`，致 `backfill` 在这两宿主上恒失败——E2E 暴露后修正为只翻译注入类响应。
- **已知不一致（如实登记）**：`checkpoint_write` 的 `session_id` 不过归一化函数（`query`/`dialogue` 经）——抽取前的既有差异，改它会变更已落库来源链值，故原样保留；记入协议文档 §2.4。
- 成本：新增两个库内模块 + 一个安装脚本 + 一份协议文档；off 路径（未设 `LANTAI_HOST`）行为逐字节不变。

## 相关

- [宿主接入协议](../host-hook-protocol.md) — 当前契约的权威描述
- [ADR-0006](0006-shell-hook-contract.md) — Shell Hook 注入契约（本机制泛化的基础）
- [ADR-0049](0049-receipt-chain.md) — 回执链（`receipt_status="acked"`）
- [ADR-0013](0013-naming-system.md) — 命名体系（本 ADR 不起新名的纪律依据）
- 调研：`docs/research/memory-landscape-2026-09/followups/2026-10/02-host-hook-integrations.md`、`roadmap-v2.md:24`
- 设计：`docs/specs/2026-09-26-host-matrix-design.md`；票据 `.scratch/host-matrix/`（spec + issues 01-05）
