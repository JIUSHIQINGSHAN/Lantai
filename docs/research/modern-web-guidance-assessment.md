# Modern Web Guidance 对兰台的价值评估

> 调研日期：**2026-11-15**。
> 范围：GoogleChrome 官方分发仓、源码仓及 Chrome 官方文档；用户已有的本地副本、Obsidian 指导和兰台当前实现。兰台代码基线为 `cf4804f0`，外部本地副本为 `4dd968349e54ca45a05dd5c818fb035c9054b022`（`Release v1.0.0`）。
> 方法：一手资料阅读、代码对应、最小事件探针及隔离的真实 Chromium 验证。本次未下载或运行上游代码、安装插件、改变产品实现或操作真实记忆库。
> 标记：**事实**为资料、源码或本次实测直接支持的陈述；**推论/建议**为采用判断；**待验证**为证据不足或尚需实测的事项。上游可变化的 `main` 与本地固定快照分别标明。

**结论：值得采用，优先作为前端开发与审查时按需查阅的资料，同时借鉴其知识组织和效果评测方法。** 兰台现有无构建 HTML/CSS/ES Modules 路线与其原生平台指导契合；本次已从指南对应出中文输入法提前提交、两个 dialog 缺少可访问名称这两类具体改进。长期价值在于让法门与认知闭环进一步验证“选中的知识是否帮助任务成功”，而不只验证内容进入了上下文。事实依据与验证边界见第四节，建议顺序见第二节。

## 一、已证实的官方资料

### 1. 项目定位与仓库分工

| 事实 | 一手依据 |
|---|---|
| Modern Web Guidance 向编码 Agent 提供 Web 平台知识、实现建议和浏览器支持信息，通过 skills、插件及 CLI 查询供开发工作使用。 | [官方概述][overview]、[开始使用][start] |
| `GoogleChrome/modern-web-guidance` 是供使用者接入的分发仓；`modern-web-guidance-src` 承担内容创作、校准、评测及工具开发。源码仓贡献文档明确说：变更编译后按周发布到只读分发仓和 npm 包。 | [分发仓 README][dist-readme]、[源码仓贡献说明][contributing] |
| 分发仓网页可见 `skills/`、多宿主插件元数据目录及 `package.json`；源码仓网页可见 `features/`、`guides/`、`skills-src/`、`src/`、`bin/` 等目录。仅目录存在不能证明特定宿主版本兼容或某个可执行文件存在。 | [分发仓][dist]、[源码仓][src] |
| 内容治理有 Chrome、Edge、Firefox/MDN 和独立专家参与。Owner 管理发布及基础设施，Content ATL 负责领域内容准确性、评测就绪和 Baseline 降级对齐。 | [GOVERNANCE.md][governance] |

**推论**：其主要价值是可检索的实现指导和经过评测的知识内容，不是必须接入页面运行时的 UI 框架、组件库或构建系统。也不能把“提供现代化指导”理解成能够自动完成所有代码迁移：官方明确没有 `legacy-scan --fix`，CI 自动化仍在开发；改代码和判断误报由 Agent 完成。[现代化工作流][workflow]

### 2. 实际可用的内容格式与入口

| 内容层 | 已确认的形式与路径 | 使用意义 |
|---|---|---|
| 使用端 skill | 分发仓 [`skills/modern-web-guidance/SKILL.md`][skill]，包含加载、搜索、检索、兼容目标和现代化审查流程。 | 是 Agent 操作规范；不是把全部指南一次性塞进上下文。 |
| 指南索引 | 官方 [guides 目录][guide-index]，另有官方文档链接的 [Markdown 文本入口][guide-index-text]。 | 可以先人工选题，再直接阅读单篇指南。 |
| CLI 检索内容 | `search` 返回 JSON，含指南 ID、说明、分类、特性引用、token 估算和相似度分数；`retrieve` 输出指定指南的 Markdown。 | 无须先安装宿主插件，也能选择单篇资料；运行 CLI 本身仍需相应运行环境。依据：[开始使用][start]。 |
| 创作端指南 | `guides/<分类>/<主题>/guide.md`；创作流程还使用 `demo.html`、`expectations.md`、frontmatter 和 Baseline fallback macros 等约定。 | 指南、演示及实现期望属于内容创作材料；源码中存在这些格式不等于用户项目自动获得测试。依据：[贡献说明及其链接的创作规范][contributing]。 |
| 特性与工具源码 | 源码仓 `features/`、`src/`、`bin/`，以及 `skills-src/`；`package.json` 提供构建、指南生成、分发及评测相关脚本。 | 维护与研究入口；普通使用者不需要因此采用源码仓开发链。依据：[源码仓][src]、[源码包元数据][src-package]。 |

已直接读到的指南源码包括以下三篇，路径可作为后续对照入口：

- [`guides/css/responsive-design/guide.md`][responsive]。
- [`guides/accessibility/accessibility/guide.md`][accessibility]。
- [`guides/ui-components/dialog/guide.md`][dialog]。

**边界**：本次未逐文件审计整个 `features/` 和 `skills-src/`，也未核验所有指南的 frontmatter/schema。源码创作规范存在编译和宏处理，因此不能把未经核对的源码与 CLI 最终输出视为逐字相同。[贡献说明][contributing]

### 3. 如何使用，以及 CLI 与 clone 的区别

**事实**：官方推荐 `npx modern-web-guidance@latest install`，也提供不先安装 skill 的 `search`、`retrieve`、`legacy-scan`。这是使用方式记录，本次未执行。[分发仓 README][dist-readme]、[开始使用][start]

```text
npx modern-web-guidance@latest search "light dismiss dialog"
npx modern-web-guidance@latest retrieve "<id>"
npx modern-web-guidance@latest legacy-scan <path> --json
```

**事实**：skill 要求先用精简的任务词搜索，按说明选择具体指南及相关通用指南后读取；可逗号分隔检索多个 ID。它建议 Windows 使用 `npx.cmd`，并给出 pnpm、离线缓存及受限网络环境的处理说明。这些是上游 skill 的使用要求，不是本次研究对兰台执行的配置变更。[SKILL.md][skill]

**事实**：本次读取的分发仓 `package.json` 声明：

- 包名 `modern-web-guidance`，版本 `1.0.0`。
- `bin` 指向 `skills/modern-web-guidance/modern-web.mjs`。
- `engines.node` 为 `>=22.0.0`，另有 `engines.vscode: ^1.90.0`。
- `repository` 指向源码仓，许可证字段为 `Apache-2.0`。

源码仓包名为 `modern-web-guidance-src`、版本 `1.0.0`、`private: true`，要求 Node `>=22.18.0`，其开发入口 `gd` 指向 `bin/gd.ts`。[分发包元数据][dist-package]、[源码包元数据][src-package]

**事实与待验证**：`bin` 声明不能证明 npm tarball 与 Git checkout 内容一致。第四节已核实本地固定快照缺少该 `.mjs`；线上调研未取得 npm 发布文件清单，本次也未执行 CLI。因此**不把 clone 等同于可运行 CLI**，不将两份 `engines` 混用；npm 实际产物、可执行入口及离线可用性留待独立验证。

**推论**：评估收益可以先靠官方单篇指南完成，不必先安装插件或改变项目工具链。若以后采用 CLI，应将“npm 发布产物核验”和“Git 内容阅读”分开验收。

### 4. 浏览器兼容策略、维护与版本边界

**事实**：官方采用 Baseline 兼容分层：Limited availability 尚未覆盖主要引擎；Newly available 达到互操作不足 30 个月；Widely available 达到互操作至少 30 个月。无项目声明时，skill 默认以 **Baseline Widely available** 为目标，该层级默认不要求 fallback；其他特性应按指南要求降级。项目可声明 Baseline 年份、具体浏览器最低版本、禁止 polyfill 等约束，覆盖默认策略。[开始使用][start]、[SKILL.md][skill]、[Baseline 官方说明][baseline]

**事实**：实验性、单引擎和 origin trial 指南需要显式 opt-in；可能涉及 flags、试用注册和不兼容变更。官方建议必要 polyfill 仅在需要的浏览器动态加载。[开始使用][start]

**事实**：推荐 CLI 安装方式支持自动更新，多数其他安装方式不具备自动更新。源码仓贡献说明记录按周编译发布；治理文件规定 Owner 负责包发布和大版本，但未给出长期支持窗口、兼容承诺或问题响应 SLA。[开始使用][start]、[贡献说明][contributing]、[治理文件][governance]

**事实**：本次读取的分发 skill 在搜索示例中带有 `--skill-version 2026_11_09-5bcc021b`，包元数据版本为 `1.0.0`。这是两种不同标识，不能互相替代。现代化工作流文档要求版本 `1.0` 或以上。[SKILL.md][skill]、[分发包元数据][dist-package]、[现代化工作流][workflow]

**待验证**：本次未成功读取 GitHub releases API，不能独立确认“最新 release/tag”、确切发布提交和当前 npm dist-tag。正文链接使用可变化的 `main`；后续若形成项目决策，应保存所采用内容的提交、包版本及指南 ID。协作者掌握的本地 revision 应在本地映射部分另行登记。

**推论**：Baseline 是选择建议和 fallback 的起点，不是兰台目标设备已经通过验收的证明。嵌入式 WebView、旧手机、辅助技术组合等要求仍需项目自己声明和测试。自动更新有利于时效，固定引用有利于复现，应按采用场景选择。

### 5. 无构建原生控制台最有价值的具体内容

以下“帮助”是基于官方内容的**推论**，不代表已检查兰台现状或发现产品缺陷。

| 优先级 | 官方已写明的具体内容 | 对原生 HTML/CSS/JS 的可能帮助与限制 |
|---|---|---|
| 高：可访问性基本盘 | 使用语义 landmark、一个 `main`、合理标题层级和 skip link；表单需要持久 label，错误/帮助文本通过 `aria-describedby` 关联；优先使用原生控件。[accessibility][accessibility] | 可直接整理成页面审查清单，无需前端框架。是否缺失必须对实际页面核验。 |
| 高：键盘与焦点 | 保持逻辑 Tab 顺序，不用正 `tabindex`；保留清晰 `:focus-visible`；交互目标至少 `24 × 24` CSS px，粗指针下更大；避免不加区分地使用 `touch-action: none`。[accessibility][accessibility] | 能把“手机能显示”扩展为键盘与触屏可操作。指南里的阈值不能代替真实设备和辅助技术检查。 |
| 高：原生 dialog | `<dialog>` 配合 `showModal()` 提供顶层、焦点约束、外部 inert、Escape 和焦点返回；需要 `aria-labelledby` 或 `aria-label` 为对话框命名，并选择安全的初始焦点。[dialog][dialog] | 对已有原生实现可逐条检查，不必引入另一套组件。不能仅凭该指南推断产品有误批准问题。 |
| 中高：移动尺寸与组件响应式 | 响应式指南推荐 `@container`、动态视口 `dvh` / `dvw`，以及用 `aspect-ratio` 为媒体预留空间；并指向 `size-aware-styling`、`fluid-scaling`、`css-layout`。[responsive-design][responsive] | 可作为窄屏与手机浏览器 UI 变化的候选方案。已读的响应式总指南较短，没有覆盖所有 overflow、触控和 fallback 细节，不宜按标题推断完整方案。 |
| 中：关闭协议与渐进增强 | Dialog 指南区分 `close()`、`requestClose()`、`cancel`、`returnValue`，建议在 `close` 中读取选择后清空值；`requestClose()` 先派发可取消事件。`closedby`、invoker commands 等需要按兼容条件渐进增强。[dialog][dialog] | 可用于设计关闭路径测试和防御性加固。具体是否会保留旧返回值、是否调用该 API，均需按产品实际调用路径验证。 |
| 中：有针对性的现代化扫描 | 扫描支持前端源码、组件及包清单，使用本地解析而非 LLM 请求；扫描规则只覆盖约 20% 指南。Agent 须根据 `keepWhen` / `dropWhen` 剔除误报，保留有意的 fallback。[现代化工作流][workflow] | 可发现部分旧模式，但不是全量质量审计；“零命中”不等于布局、可访问性和业务逻辑都正确。 |

官方目录还列出 [Motion][motion-page]、[HTML][html-page]、[Single-line text overflow][overflow-page] 等可能相关主题；本次只核实目录及描述，**未阅读全文，不据此给出具体实现结论**。[指南目录][guide-index]

### 6. 浏览器测试：可以借鉴什么，不能直接得到什么

**事实**：上游把指南内容与实现期望关联，用 Playwright 测试评估实现；官方“开始使用”说明校准要求为：参考实现全部通过、刻意破坏的实现全部失败，之后再进行有/无指导的应用级评测。[开始使用][start]。贡献说明将内容创作、浏览器验证和 Playwright grader 生成/校准划为不同阶段，并链接 `src/harness/README.md`、`docs/EVALS.md` 等详细材料；本次未继续逐项核验这些实现。[贡献说明][contributing]

**事实**：现代化工作流要求修改后重新扫描改动文件并运行项目自己的测试，建议结合浏览器工具观察运行时。已读官方目录没有显示独立的通用“浏览器测试套件”条目。[现代化工作流][workflow]、[指南目录][guide-index]

**事实**：可访问性指南推荐 `axe-core`、Lighthouse，也要求键盘和屏幕阅读器人工检查；明确自动评分不足以证明真实可用性。它列举的组合包括 NVDA/Firefox、VoiceOver/Safari、TalkBack/Chrome 等，已读 `guide.md` 本身没有附带可执行的项目测试套件。[accessibility][accessibility]

**推论**：更值得借鉴的是“指南 → 可观察期望 → 正确示例和错误示例 → 浏览器验证”的组织方式。它可以帮助细化焦点、关闭结果、尺寸和滚动等断言，但不会自动生成适合兰台的用例、设备矩阵或 CI 门禁。上游的 Agent 遵循度评测也不能换算为本项目的缺陷减少比例或性能收益。

### 7. 许可证

**事实**：分发仓 `LICENSE` 为 **Apache License 2.0**，包元数据也声明 `Apache-2.0`。分发及派生内容需遵守许可证、适用署名/声明保留和修改标记等要求；若原作品含 NOTICE，相关声明亦有保留条件。许可证包含专利授权及终止条款，并按 AS IS 提供、无适用性保证；不授予一般商标使用权。[LICENSE][license]、[package.json][dist-package]

**边界**：此处核实的是仓库许可证。Chrome 文档站页面、指南引用的第三方材料、示例链接的 polyfill 各有其权利来源，不能仅凭仓库许可统一认定。本文未逐一审查这些外部依赖的许可证。

## 二、建议采用顺序

以下为**建议**，尚未创建实现票据或修改接口。

| 顺序 | 最小可交付事项 | 具体收益与验收方式 |
|---|---|---|
| 1 | 中文输入法 Enter 防护 | 对终端、检索演练、档案搜索的自定义 Enter 处理补组合态防护。验证组合中的 Enter 不请求、不清空输入，普通 Enter 正常提交一次；另用真实中文输入法和目标浏览器确认候选字流程。证据见第四节。 |
| 2 | 两个原生 dialog 的可访问名称 | 为现有标题建立 `aria-labelledby` 关联，核验安全初始焦点及关闭后焦点返回。用真实 accessibility tree 断言名称非空，并验证确认、取消、Escape 的结果；`returnValue` reset 可一起作为防御性加固，不能宣称当前 Escape 已误批准。 |
| 3 | 把已有布局回归扩展到实际交互 | 保留 320/1440 CSS 回归，按改动增加真实产品脚本的 Tab/方向键、200% 文字缩放、长文本、减少动态效果和抽屉开关验证；声明其他浏览器支持时再覆盖对应 fallback。 |
| 4 | 下一张前端票按需查具体指南 | Obsidian 笔记负责导航，具体 guide 负责规则和 fallback，项目测试负责验收。先用现有本地资料即可；需要 CLI 检索时再核验 npm 产物、版本、离线和遥测设置。 |
| 5 | 做一个小规模“知识是否改善任务”试点 | 选择输入法、dialog、窄屏三类真实任务，对比无资料、只有摘要、按需读取具体指南。记录任务成功、误触发、上下文用量与耗时，再决定是否调整法门检索返回形态。 |

**暂缓的采用方向**：全面替换样式/组件、为了 `focusgroup` 等能力增加 polyfill、浏览器内建 AI、WebMCP、把全部指南导入长期记忆。这些与当前最小收益没有必要依赖关系，且需要另行验证兼容性、架构契约和维护成本。Scanner 仅在整体旧模式审查时作为辅助工具，不能替代项目门禁。[SKILL.md][skill]、[现代化工作流][workflow]、[ADR-0025](../adr/0025-console-no-build-modules.md)

## 三、待验证与工具限制

- 官方线上调研中 `gh` 不可用，GitHub API 的递归 tree 和 releases 请求返回 403；改用官方网页及 raw 文件取证。部分 WebFetch 请求另遇超时/502，未将失败响应作为依据。
- 本地快照的 `.mjs` 缺失已核实，209 篇发布指南已按 Git 跟踪文件计数；npm tarball 文件清单、当前 dist-tag、CLI 运行效果、安装器副作用、宿主插件兼容性和离线行为仍未验证。
- 未运行 scanner、上游示例或上游评测；未复跑上游分数，也未证明它们可迁移到兰台。未审计全部 209 篇指南。
- 本地快照是 `v1.0.0` 发布提交，不据此宣称线上仍为最新版本；未来采用时应记录实际包版本、指南 ID 和内容 revision。
- 本次仅有最小 Node 事件探针与隔离 Chrome 验证；没有真实 OS 输入法、Firefox/WebKit、屏幕阅读器或设备矩阵验收，也未运行全量 pytest、release check。探针结果不等于完整前端或发布门禁通过。
- 响应式总纲或目录收录不足以证明所有手机布局问题都有适用方案；应按实际问题读取子指南并验证。

## 四、本地副本、Obsidian 与兰台对应

### 1. 已有资料足以开始使用

**事实**：本次核对的本地副本为干净的 `main`，HEAD `4dd968349e54ca45a05dd5c818fb035c9054b022`，提交日期 2026-11-10、说明 `Release v1.0.0`。该快照跟踪 `skills/modern-web-guidance/SKILL.md` 和 **209 篇** `guides/` Markdown；计数指文件数，不代表 209 个浏览器 API。依据：本地 `git status`、`git log`、`git rev-parse` 与 `git ls-files`。

**事实**：已有同 revision 的 Obsidian 摘要，覆盖查询纪律、Baseline/fallback、HTML、布局、表单、性能、安全和验证。它只提供阅读入口，不会自动加载进会话；具体规则以固定快照为准。

**事实**：本地 `package.json` 的 bin 指向 `skills/modern-web-guidance/modern-web.mjs`，但该文件在此工作树不存在、也未被 Git 跟踪。因此这份 clone 能直接用于读指南，不能照入口声明运行本地 CLI。这个结论只针对已检查的 Git 快照，不证明 npm 包缺文件。[固定快照 package.json][snapshot-package]

**建议**：保持“Obsidian 做入口 → 具体指南提供依据 → 项目真实测试验收”的分工。无需再复制一份同内容的总纲；后续更新时先对照 revision，再刷新相关规则。若未来使用官方 CLI，查询用通用能力词即可，不带私人数据或产品正文。分发仓 README 说明搜索匹配在本地完成，但另有收集搜索词、guide ID 等匿名使用信息的遥测，并提供 `DISABLE_TELEMETRY=1`；“本地检索”与“没有任何出站”须分开理解。[README 的 How It Works 与 Telemetry & Privacy][snapshot-readme]

### 2. 与兰台现有架构契合，已有基础应继续沿用

| 已核实的兰台现状 | 对应指南与采用判断 |
|---|---|
| [ADR-0025](../adr/0025-console-no-build-modules.md) 明确 FastAPI 同源托管原生 HTML/CSS/ES Modules，无 Node 构建、运行时 CDN 或第三方框架。 | 指南以原生 HTML/CSS/JS 为基础，适合直接查阅。开发时选择 CLI 不等于给页面或 wheel 增加运行依赖。[SKILL.md][snapshot-skill] |
| [dom.js](../../lantai/api/ui/dom.js) 的 `node()`、`emptyState()`、记忆卡与 toast 使用 DOM 构造和 `textContent`。 | 与不可信文本安全渲染方向一致。当前展示纯文本，无需为了使用 Sanitizer API 引入富文本或新依赖。[sanitize-untrusted-html][snapshot-sanitize] |
| [index.html](../../lantai/api/ui/index.html) 已有 skip link、原生 dialog、`color-scheme` meta、移动导航；[styles.css](../../lantai/api/ui/styles.css) 有 root `color-scheme`、可见焦点、减少动态效果和安全区域处理。 | 指南可以补查遗漏；本次没有理由替换吉金/漏窗主题。动态系统主题、对比度、缩放等仍需针对目标行为验证。[accessibility][snapshot-a11y]、[dark-mode][snapshot-dark] |
| [studio.js](../../lantai/api/ui/studio.js) 实现了 tab 的 ARIA 状态、roving tabindex 和方向键/Home/End；[monitor.js](../../lantai/api/ui/monitor.js) 已暂停隐藏页轮询并在离开视图时清定时器。 | 已存在相应行为，不应只为使用新 API 重写。上游 `tabs` 的 `focusgroup` fallback 涉及外部 polyfill，与项目约束存在取舍，应保留可工作的现有方式或另作决策。[tabs][snapshot-tabs] |
| [tests/test_studio_ui.py](../../tests/test_studio_ui.py) 已运行真实 Chromium 测量两个 Studio 面板 320px 单栏/1440px 双栏、整页无横向溢出。夹具移除了产品脚本，主要验证真实 HTML/CSS。 | 这是有价值的布局回归，但没有执行产品事件处理。后续可按改动补交互、焦点与 accessibility tree；不能把这个测试称为完整 UI 端到端验收。依据：该文件第 49–149 行。 |

### 3. 本次发现并验证的具体改进

#### 中文输入法确认候选字会触发业务动作

**事实**：三处自定义 Enter 处理没有组合态保护：

- [terminal.js:9](../../lantai/api/ui/terminal.js) 直接 `sendTerminalChat()`；该函数会清空输入并发送请求。
- [playground.js:191](../../lantai/api/ui/playground.js) 直接触发检索与探针请求。
- [vault.js:268](../../lantai/api/ui/vault.js) 直接触发档案搜索。

上游 [`ime-safe-enter-submit`][snapshot-ime] 明确指出中文、日文、韩文输入法用 Enter 确认候选字，自定义 handler 应检测组合态；单行搜索也适用。它还提供 Safari 事件时序的回退说明，具体选择须依据目标浏览器。

**本次实测**：Node v24.22.1 直接 import 以上产品模块，使用最小 DOM 和网络替身，向真实注册的 handler 传入 `key='Enter', isComposing=true, keyCode=229`。没有替换 handler 或内部提交逻辑。

| 入口 | 观察结果 |
|---|---|
| 终端 | 发起 1 次请求，输入被清空；普通 Enter 对照也发起 1 次请求。 |
| 检索演练 | 发起 `/search` 与 `/probing/detect` 两次请求。 |
| 档案搜索 | 发起 `/search` 一次请求。 |

**判定**：组合态被忽略已在产品事件路径证实；用户可见的风险是候选字尚未确认就提交未完成文本，终端还会清空输入。真实 OS 输入法和跨浏览器流程仍需实现票验证。本次没有执行真实网络请求，也没有改代码。

#### 两个 dialog 的可访问名称为空

**事实**：`index.html:673` 的 `actionDialog` 与 `:682` 的 `connectionDialog` 虽有可见标题，dialog 元素没有 `aria-labelledby` / `aria-label`。上游要求对每个 dialog 建立可访问名称。[dialog][snapshot-dialog]

**本次实测**：在仅含原样产品 dialog HTML 的 `127.0.0.1` 隔离页面中，使用 HeadlessChrome 157 的原生 `showModal()` 和 CDP `Accessibility.getFullAXTree`：两个节点均为 `role='dialog', name=''`。因此这里不只依据静态属性作判断。

**建议**：为标题提供稳定 ID，并以 `aria-labelledby` 关联；可在同一票验证初始焦点、取消、Escape 和焦点返回。尚未进行真实屏幕阅读器测试。

#### returnValue 清理属于防御性加固

**事实**：`app.js:562–579` 的 `ask()` 读取 `returnValue` 后没有 reset。上游指出某些关闭请求不会提供新值，应读完清空，避免旧结果泄漏。[dialog 的 Hooking into close][snapshot-dialog]

**实测区分**：用原样产品 `ask()` 和 dialog HTML，在隔离 Chrome 页面测试：第一次 `close('confirm')` 后，第二次无参 `requestClose()` 保留旧值，得到 `confirmed=true`；另用 CDP `Input.dispatchKeyEvent` 实际发送 Escape，得到 `confirmed=false`，显式 `close('cancel')` 同样为 false。

**判定**：本次检查的产品脚本未调用 `requestClose()`，不能据此宣称现有用户按 Escape 会误批准，也没有触发真实审批 API。reset 值可作为小幅加固，真实可达缺陷的结论限于前两项。

### 4. 对兰台长期知识与评测的帮助

这部分是基于已核实结构提出的**建议**，不是已落地功能或已测收益。

**让法门更清楚地表达适用边界，并按需取全文。** 现有 [FamenEngine](../../lantai/cognition/famen.py) 已归一 `trigger_rule`、`procedure`、`steps` 和 metadata，[REST 路由](../../lantai/api/routes_famen.py) 已有 match/list/detail。`match_skills()` 当前按名称、触发文本与步骤词元评分，并直接返回含完整步骤的序列化结果；未见独立的“不适用条件”字段。因此可借鉴上游 skill 的明确触发/排除范围，以及先返回 ID/简介、选中后取 guide 的流程，先验证能否减少误触发和无关全文占用，再讨论契约/Schema 变更。[本地法门依据](../../lantai/cognition/famen.py)、[上游检索纪律][snapshot-skill]

**让知识条目带条件、来源、误用边界和验收方法。** 针对会随平台变化的工程经验，可以试行记录来源 revision、最后验证时间、适用环境、fallback 和可观察结果。上游指南的兼容分层、Do/Don't 与回退结构提供了范例；这些是知识内容组织经验，不要求把 Web API 字段原样搬进兰台领域模型。[SKILL.md][snapshot-skill]、[IME guide][snapshot-ime]、[dialog][snapshot-dialog]

**把上下文代理指标与任务结果分开测。** [BL-04/BL-05 测试](../../tests/cognitive/test_cognitive_loop.py) 第 195–274 行分别检查 lesson 关键词进入 prompt、学习后 failures/beliefs 切面增多。这些是有效的链路/上下文代理验证；要证明实际任务改善，还需执行任务并检查产出。上游有/无 guidance 对照、参考正确实现通过/错误基线失败的 grader 校准方式可借鉴，但无需照搬其全部 harness。[上游 Evals to prove this works well][snapshot-readme]

建议先对输入法、dialog、窄屏任务各准备独立变体，对比三组：无资料、仅 Obsidian 摘要、按需读取具体指南。固定模型/版本、初始仓与工具预算，分别记录检索是否选对、最终行为是否通过、上下文用量、延迟及回归。保留不命名 API 的自然需求和未见变体，避免测成“照抄答案”；先验证 grader 能区分正确/破坏实现。只有看到项目内稳定收益，才考虑把经验推广到法门默认流程。

### 5. 采用边界与本次完成范围

- `tabs` 等新能力可能要求 polyfill，而兰台已有可工作的键盘逻辑；不应仅因指南推荐更新 API 就增加供应链。容器查询、`dvh` 等也应以具体布局问题驱动，不批量替换所有 media query。[tabs][snapshot-tabs]、[size-aware-styling][snapshot-size]
- Obsidian 提醒的指南内部矛盾已核对：固定快照 `html.md:149` 禁止 hero/LCP 图 lazy-load，而其 `hero-desktop.webp` 示例 `:187–192` 使用了 `loading='lazy'`。采用时应核对规则和上下文，不能因来源权威而机械复制示例。[固定 HTML 总纲][snapshot-html]
- 本次只新增本研究文件，并在会话持久记忆保存资料路径；没有改产品代码、AGENTS、设置、Obsidian 原文或外部仓，没有安装、导入知识、提交、推送或发布。
- 最小 Node 探针早期因 DOM 替身不全失败，补齐后得到上述有效输出；data URL 和部分 `--dump-dom` 探针未产出有效结果，不作为成功证据。最终关闭与无障碍结论来自成功的本地 HTTP/CDP 验证。
- 本次未跑完整测试或发布门禁，不能据此推断分支全绿；后续实现应按现有票据、TDD、核心真冒烟、审查及发布纪律进行。

## 来源

以下均为官方拥有的仓库或文档；引用的 raw 文件与同路径 GitHub 页面对应。未成功返回的 API 不作为事实依据。

[overview]: https://developer.chrome.com/docs/modern-web-guidance
[start]: https://developer.chrome.com/docs/modern-web-guidance/get-started
[workflow]: https://developer.chrome.com/docs/modern-web-guidance/modernization-workflow
[guide-index]: https://developer.chrome.com/docs/modern-web-guidance/guides
[guide-index-text]: https://developer.chrome.com/docs/modern-web-guidance/guides.md.txt
[dist]: https://github.com/GoogleChrome/modern-web-guidance
[dist-readme]: https://github.com/GoogleChrome/modern-web-guidance/blob/main/README.md
[src]: https://github.com/GoogleChrome/modern-web-guidance-src
[skill]: https://github.com/GoogleChrome/modern-web-guidance/blob/main/skills/modern-web-guidance/SKILL.md
[dist-package]: https://github.com/GoogleChrome/modern-web-guidance/blob/main/package.json
[src-package]: https://github.com/GoogleChrome/modern-web-guidance-src/blob/main/package.json
[contributing]: https://github.com/GoogleChrome/modern-web-guidance-src/blob/main/CONTRIBUTING.md
[governance]: https://github.com/GoogleChrome/modern-web-guidance-src/blob/main/GOVERNANCE.md
[license]: https://github.com/GoogleChrome/modern-web-guidance/blob/main/LICENSE
[responsive]: https://github.com/GoogleChrome/modern-web-guidance-src/blob/main/guides/css/responsive-design/guide.md
[accessibility]: https://github.com/GoogleChrome/modern-web-guidance-src/blob/main/guides/accessibility/accessibility/guide.md
[dialog]: https://github.com/GoogleChrome/modern-web-guidance-src/blob/main/guides/ui-components/dialog/guide.md
[motion-page]: https://developer.chrome.com/docs/modern-web-guidance/guides/ui-behaviors/motion
[html-page]: https://developer.chrome.com/docs/modern-web-guidance/guides/html/html
[overflow-page]: https://developer.chrome.com/docs/modern-web-guidance/guides/ui-atoms/single-line-overflow
[baseline]: https://web.dev/baseline
[snapshot-package]: https://github.com/GoogleChrome/modern-web-guidance/blob/4dd968349e54ca45a05dd5c818fb035c9054b022/package.json
[snapshot-readme]: https://github.com/GoogleChrome/modern-web-guidance/blob/4dd968349e54ca45a05dd5c818fb035c9054b022/README.md
[snapshot-skill]: https://github.com/GoogleChrome/modern-web-guidance/blob/4dd968349e54ca45a05dd5c818fb035c9054b022/skills/modern-web-guidance/SKILL.md
[snapshot-ime]: https://github.com/GoogleChrome/modern-web-guidance/blob/4dd968349e54ca45a05dd5c818fb035c9054b022/skills/modern-web-guidance/guides/forms/ime-safe-enter-submit.md
[snapshot-dialog]: https://github.com/GoogleChrome/modern-web-guidance/blob/4dd968349e54ca45a05dd5c818fb035c9054b022/skills/modern-web-guidance/guides/ui-components/dialog.md
[snapshot-a11y]: https://github.com/GoogleChrome/modern-web-guidance/blob/4dd968349e54ca45a05dd5c818fb035c9054b022/skills/modern-web-guidance/guides/accessibility/accessibility.md
[snapshot-dark]: https://github.com/GoogleChrome/modern-web-guidance/blob/4dd968349e54ca45a05dd5c818fb035c9054b022/skills/modern-web-guidance/guides/visual-design/dark-mode.md
[snapshot-tabs]: https://github.com/GoogleChrome/modern-web-guidance/blob/4dd968349e54ca45a05dd5c818fb035c9054b022/skills/modern-web-guidance/guides/ui-components/tabs.md
[snapshot-sanitize]: https://github.com/GoogleChrome/modern-web-guidance/blob/4dd968349e54ca45a05dd5c818fb035c9054b022/skills/modern-web-guidance/guides/security/sanitize-untrusted-html.md
[snapshot-size]: https://github.com/GoogleChrome/modern-web-guidance/blob/4dd968349e54ca45a05dd5c818fb035c9054b022/skills/modern-web-guidance/guides/css/size-aware-styling.md
[snapshot-html]: https://github.com/GoogleChrome/modern-web-guidance/blob/4dd968349e54ca45a05dd5c818fb035c9054b022/skills/modern-web-guidance/guides/html/html.md
