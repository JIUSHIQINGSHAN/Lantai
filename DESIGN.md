# Lantai Studio · OpenDesign System Contract (DESIGN.md)

> **版本**: 1.0.0  
> **设计哲学**: 典籍意象（Imperial Archives）× 现代化工程美学（Linear / Apple 级质感）  
> **设计规范**: Local-first, Agent-Native, Token-Driven, Craft Layer Enforced.

---

## 🏛️ 1. 意象与调性 (Atmosphere & Brand Identity)

兰台（Lantai）作为智能记忆中枢，其界面承载着「为 AI 记录与追溯文明记忆」的厚重感与严谨性。

### 双典籍主题 (Dual Classical Themes)
1. **「吉金」（Jijin · 深色 / Dark Theme · 默认）**
   - **基调**：先秦青铜鼎与暗金铭文；
   - **调色**：深青黛背景 (`#131a22`)、青铜案台面板 (`#1a232c`)、玄青表面 (`#23303c`)、吉金琥珀亮色 (`#c69b51`)。
2. **「漏窗」（Louchuang · 浅色 / Light Theme）**
   - **基调**：江南古典园林的花窗漏影与宣纸竹墨；
   - **调色**：素练纸白 (`#f3f6f3`)、粉墙灰台 (`#e8eee9`)、竹青雅灰 (`#d7e2d9`)、沉香金栗 (`#9e7839`)。

---

## 🎨 2. 视觉设计系统 Tokens (Design Tokens)

### 2.1 颜色语义 (Color Semantics)
- `--bg`: 宇宙/画布基底底色；
- `--panel`: 侧边栏与主工作卡片容器底色；
- `--surface`: 嵌套卡片、输入框与次级面板底色；
- `--surface-strong`: 高亮交互、激活状态背景；
- `--ink`: 主文本色彩（高对比度，保证可读性）；
- `--muted`: 辅助元数据、时间戳、说明文字；
- `--line`: 极细微边框（1px 细线，不喧宾夺主）；
- `--accent`: 主行动点（CTA）、主品牌高亮（金栗/青铜金）；
- `--green`: 稳态、已应用、低风险（翡翠）；
- `--red`: 冲突、严重风险、删除/拒绝（丹砂）；
- `--blue`: 系统任务、演化建议（霁蓝）；
- `--focus`: 键盘导航与聚焦发光环。

### 2.2 排版与字体阶梯 (Typography Scale)
- **品牌与大标题**：`"KaiTi", "STKaiti", "Songti SC", serif`（典雅书法意象，字号 20px–24px）；
- **主功能文本**：`"Segoe UI", -apple-system, BlinkMacSystemFont, "Microsoft YaHei", sans-serif`（清爽高辨识度，字号 13px–14px，行高 1.5）；
- **代码、ID 与打分指标**：`ui-monospace, "Cascadia Mono", "Fira Code", monospace`（严格等宽对齐，字号 11px–12px）。

### 2.3 间距与圆角 (Spacing & Radius)
- **圆角标尺**：按钮/标签 `6px`，卡片 `10px`–`12px`，大容器 `14px`；
- **间距网格**：基于 4px/8px 倍数（`4px`, `8px`, `12px`, `16px`, `20px`, `24px`）。

---

## 🧩 3. 核心组件范式 (Component Manifest)

1. **TopBar & Breadcrumb (顶栏工作区)**:
   - 左侧展示当前视界名称与即时副标；
   - 右侧集成主题快切、API 连接指示灯与主动作按钮（Primary Action）。
2. **Segmented Tab Switcher (胶囊式选项卡)**:
   - 紧凑型胶囊外观，切换时具备平滑透明度与微平移动画。
3. **Metric Card (指标卡片)**:
   - 上半部分为大字号等宽数字/状态值，下半部分为辅助说明，悬浮时微浮起。
4. **Master-Detail Triage Hub (案牍双栏协同)**:
   - 左侧卡片流展示优先级指示条、风险徽章与相对时间；
   - 右侧抽屉展示字段 Diff 对比（`del` 红色底色，`ins` 绿色底色）与多维操作组。
5. **Interactive Score Card (检索得分卡片)**:
   - 综合得分大标签 + 属性元数据拆解（时效衰减、置信度、重要性、版本）。

---

## 📐 4. 工艺准则 (Craft Guidelines)

- **无突兀跳动**：所有状态切换具备 `0.15s ease` 微过渡；
- **自适应暗浅色**：原生支持 `data-theme` 属性无缝重绘，拒绝亮暗失衡；
- **零外部庞大框架依赖**：纯 CSS 变量与原生现代 ES Module，极速秒开、开箱即用。
