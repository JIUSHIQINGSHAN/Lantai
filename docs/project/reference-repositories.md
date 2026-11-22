# 本项目的参考仓

两套参考资料保存在此工作区的 [Matt 技能库](../../.references/mattpocock-skills/README.md) 和 [Modern Web Guidance](../../.references/modern-web-guidance/README.md)。由根目录 [AGENTS](../../AGENTS.md) 引导按任务读取，适用范围限于此检出。来源、固定提交和全部 29 个入口以 [参考仓清单](reference-repositories.json) 为准。

## 读取方式

先读本项目对应规则，再选择下表的入口。只加载当前任务需要的正文及其必要引用；参考仓的安装命令、自动更新或全局链接流程须另有明确任务才执行。标为 `disable-model-invocation: true` 的入口仅在用户明确调用时使用。

| 工作 | 本地参考入口 |
| --- | --- |
| 了解 Matt 技能如何组合 | [ask-matt](../../.references/mattpocock-skills/skills/engineering/ask-matt/SKILL.md) |
| 需求与规格 | [grill-with-docs](../../.references/mattpocock-skills/skills/engineering/grill-with-docs/SKILL.md)、[to-spec](../../.references/mattpocock-skills/skills/engineering/to-spec/SKILL.md) |
| 拆分与推进票据 | [to-tickets](../../.references/mattpocock-skills/skills/engineering/to-tickets/SKILL.md)、[wayfinder](../../.references/mattpocock-skills/skills/engineering/wayfinder/SKILL.md)、[implement](../../.references/mattpocock-skills/skills/engineering/implement/SKILL.md) |
| 故障与测试 | [diagnosing-bugs](../../.references/mattpocock-skills/skills/engineering/diagnosing-bugs/SKILL.md)、[tdd](../../.references/mattpocock-skills/skills/engineering/tdd/SKILL.md) |
| 架构与术语 | [codebase-design](../../.references/mattpocock-skills/skills/engineering/codebase-design/SKILL.md)、[domain-modeling](../../.references/mattpocock-skills/skills/engineering/domain-modeling/SKILL.md) |
| 审查、PR 与交接 | [code-review](../../.references/mattpocock-skills/skills/engineering/code-review/SKILL.md)、[pr](../../.references/mattpocock-skills/skills/engineering/pr/SKILL.md)、[handoff](../../.references/mattpocock-skills/skills/productivity/handoff/SKILL.md) |
| 浏览器功能与页面交互 | 先读 [项目 Web 流程](../agents/web-guidance.md)，再按 2–5 个通用英文词搜索 [MWG 指南目录](../../.references/modern-web-guidance/skills/modern-web-guidance/guides/)；入口说明见 [MWG](../../.references/modern-web-guidance/skills/modern-web-guidance/SKILL.md) |
| 浏览器扩展专项 | [chrome-extensions](../../.references/modern-web-guidance/skills/chrome-extensions/SKILL.md) |

当前客户端已提供同名技能时，按宿主规则调用其入口；本地参考可用于核对便携包固定版本。若两份内容不同，记录所用来源，避免把不同版本的步骤混用。客户端没有相应技能工具时，可以读取本地文件作为流程参考，不能据此声称已注册斜杠命令或原生技能。

项目已有 [本地票据](../agents/issue-tracker.md)、[分诊标签](../agents/triage-labels.md) 和 [领域文档布局](../agents/domain.md)，继续沿用这些配置。套件固定版的初始化模板如建议使用 GLOSSARY，本项目仍采用已登记的 [CONTEXT](../../CONTEXT.md)，改名另按项目迁移规则执行。

## 本地隔离

- 两个目录是独立 Git 副本，固定到清单提交，保留各自许可证。它们仅作为参考，产品启动、测试、构建和发布不加载这些目录。
- 根目录 [.gitignore](../../.gitignore) 的 `/.references/` 排除副本正文与嵌套 Git 目录；导航及来源清单可以单独审阅提交。
- 本次未创建用户目录链接、修改全局技能或安装客户端插件，其他项目的配置保持原样。
- 固定副本不会自动更新。需要升级时先核对目标提交、差异与许可证，再更新本地检出及清单。

## 在其他检出恢复

普通克隆不会包含被忽略的参考正文。需要在另一个已授权检出使用时，按来源清单逐仓恢复；从项目根目录执行，遇到已存在目录先检查，不覆盖：

```powershell
git clone --no-checkout https://github.com/JIUSHIQINGSHAN/mattpocock-skills.git .references/mattpocock-skills
git -C .references/mattpocock-skills checkout --detach abeeaf95660ba9e8b7c753eb38f441ca707a8823

git clone --no-checkout https://github.com/JIUSHIQINGSHAN/modern-web-guidance.git .references/modern-web-guidance
git -C .references/modern-web-guidance checkout --detach 64a6d708f5a43f2cd3fde5d4d7db3905ba1bdf65
```

每次检查 `git -C <参考目录> rev-parse HEAD` 与清单相同、`git -C <参考目录> status --porcelain` 无输出，再读取资料。Git clone 保留仓库文件，不等于安装 MWG npm CLI；本项目默认使用文件检索，只有实际运行过 CLI 才记录其执行证据。
