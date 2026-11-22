# Web 前端按需查阅

修改 HTML、CSS、DOM、浏览器 API 或客户端交互时，在实现前执行下列流程。混合任务只对浏览器部分执行；纯后端、CI、脚本和工具配置沿用[开发工作流](../development-workflow.md)。本文件是文档路由，不表示已安装插件或启用自动执行。

## 执行流程

1. **约束**：在当前票 Comments 写出用户可见行为、浏览器目标和可观察验收。遵循 [ADR-0025](../adr/0025-console-no-build-modules.md) 的原生 HTML/CSS/ES Modules、无构建、同源资产约束；展示不可信正文沿用纯文本 DOM。以现有主题和交互为起点。完成标志：需求与验收一一对应。
2. **定位**：把需求拆成单一能力，用 2–5 个通用英文词查找，例如 `confirm modal dialog` 或 `responsive component layout`。先读匹配的具体指南，再补相关总纲；弱匹配换词一次，仍无覆盖则记明并查平台官方资料。完成标志：已读文件、guide ID 和 revision 可追溯，或明确记为无覆盖。
3. **取舍**：逐项把适用规则映射到改动及验收；记录未采用规则与理由。单篇代码示例与明确规则冲突时，回溯上下文、官方平台文档和实际行为再决定。完成标志：选用能力及 fallback 满足本票目标和现有项目约束。
4. **验证**：按本票先红后绿和真实核心冒烟纪律实施，执行受影响测试。浏览器行为用真实 DOM、产品脚本、请求边界、焦点或 accessibility tree 验证；静态属性检查只证明属性。完成标志：保留命令、退出码、日志路径、环境及未测边界，失败已有归因。
5. **留证**：用下方记录格式追加当前票 Comments，再按原开发流程审查与收口。完成标志：每条采用规则都有实际结果；缺证据保持待验。

## 资料分支

优先使用本项目 [MWG 参考副本](../../.references/modern-web-guidance/README.md)，位置和固定提交见 [参考仓清单](../project/reference-repositories.json)，恢复说明见 [本项目参考仓](../project/reference-repositories.md)。已有外部资料与历史 revision 仍见[研究报告第四节第1项](../research/modern-web-guidance-assessment.md#1-已有资料足以开始使用)。个人盘符只作阅读导航，不成为产品配置或 CI 依赖。具体 guide 提供规则，项目测试提供结果，各保留原出处。

- **本地文件 / 断网**：先核对所选参考仓的 `git rev-parse HEAD` 与工作区状态，再用文件名、目录或内容检索 `skills/modern-web-guidance/guides/` 并直接读全文。记录途径为“文件检索”，revision 使用实际检出值；本项目固定值见参考仓清单。历史快照 `4dd968349e54ca45a05dd5c818fb035c9054b022` 仅对应下方已有采用记录，不与当前副本混标。Git clone 提供指南文件，CLI 能否运行另行核验。
- **在线查读**：用[官方指南目录](https://developer.chrome.com/docs/modern-web-guidance/guides)导航，或读取与当前项目副本相同的[固定提交](https://github.com/GoogleChrome/modern-web-guidance/tree/64a6d708f5a43f2cd3fde5d4d7db3905ba1bdf65/skills/modern-web-guidance/guides)。记 URL、访问日期和 revision；动态页面无版本时明确记“未固定”，复现采用保存的内容 hash。网络失败转本地分支并保留失败记录。
- **可选 CLI**：确有文件检索之外的需要时，另列可审查步骤，先核对 npm 实际发布产物与可执行入口、该产物 Node 要求、安装目的地、遥测开关及离线行为，再在已有授权内查读。本流程不运行安装器、不改 harness settings；需要安装或配置时另行明确范围。查询只用通用词，正文、密钥和个人资料留在本地。`DISABLE_TELEMETRY=1` 是上游提供的退出开关，不等于已验证所有出站被阻断。真实跑过 CLI 才记 semantic search/retrieve，命令形式及披露要求按所用版本的 SKILL.md 核对。

无需全仓扫描或把全部指南导入长期记忆；开放式现代化审查另按上游扫描工作流和项目范围执行。

## 选型与支持范围

默认选型建议优先 Baseline Widely available；未达 Widely 时按具体指南采用无外部依赖的渐进增强、低成本 fallback，或保留已有实现。若指南要求外部 polyfill，与本项目约束一起评估，不能直接复制 CDN / npm 示例。实验性、Origin Trial 或 volatile 功能仅在明确 opt-in 范围内采用。

Baseline 表示选型依据。首轮自动回归目标为 Chromium，实际覆盖以当票日志为准；此处不宣布 Safari/Firefox 最低版本。合成组合事件不等于原生输入法，移动 viewport 不等于真机，AX 名称不等于屏幕阅读器验收。新票若改变对外浏览器承诺、降级或安全策略，先按开发工作流立 ADR。

## 每票记录

```text
需求 / 目标浏览器：
途径：文件检索 / 在线查读 / 实际 CLI；查询词：
来源：guide ID + 相对路径或 URL + revision + 内容 hash（如保存）
采用：具体规则 → 改动 → 可观察断言
未采用 / fallback：规则、原因、适用边界
实测：命令、退出码、日志路径、浏览器/环境版本
未测 / 阻塞：
```

## 实际采用示例（2026-11-15）

以下是已执行记录；详细红相、命令与测试边界见[诊断记录](../memory-quality/web-guidance-ui-diagnosis-2026-11-15.md)。资料途径为本地文件检索，revision 均为 `4dd968349e54ca45a05dd5c818fb035c9054b022`，没有运行官方 CLI。

| 改动 | 指南与取舍 | 实测与边界 |
|---|---|---|
| 输入法确认键 | `ime-safe-enter-submit`：三入口在组合态或229时不提交，普通Enter才preventDefault；保留组合确认默认动作，未机械照抄示例的取消顺序。 | 真实产品JS/Chromium，初始红测4请求，修复后组合/229零请求且保留输入；普通Enter和按钮次数/payload准确。原生OS输入法、WebKit未验。 |
| 原生弹窗 | `dialog`/`accessibility`：标题关联、原生autofocus、读关闭结果再清空；保留原生模态和焦点返回，未加自制焦点陷阱。 | 真AX名称、初始字段/安全取消焦点、Escape与显式取消零审批、确认一次、再次取消、隔离storage保存/清除已验证。AX通过不代表屏幕阅读器合规。 |

合并定向命令见诊断记录：46 passed；全量和发布结果不由这份流程文档背书。在线查阅曾遇TLS/连接失败，随后Chrome官方dialog入口的WebFetch成功返回“DO name every dialog with aria-labelledby pointing at its heading”等规则（返回页包含webstatus.dev能力链接，未固定在线内容）。采用依据仍固定本地快照，网络异常时本地分支可完成定位与阅读。
