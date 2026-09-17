---
feature: key-rotation-runbook
status: delivered
updated: 2026-08-31
branch: docs/key-rotation-runbook
commits: 66deafb06faf608fae4d867501b6095220606477..f40439c27ec2cb018e72ffac3e95fafa18e69de5
---

# 密钥轮换 Runbook（本机 + 服务器）

## Report

**What was built** — 新增 `docs/key-rotation-runbook.md`：分清 `OPENAI_API_KEY`（硅基流动）与 `API_KEY`（兰台 REST），给出五阶段步骤（控制台作废新建 → 本机 `.env` → 服务器重启 → `/add`+`/search` 冒烟 → 可选 git-filter-repo 清历史）及轮换后检查清单。`docs/ops-runbook.md` 增加交叉引用。

**Verification** — 文档文件存在；ops-runbook 链接已指向 `key-rotation-runbook.md`；无代码变更，未跑测试套件。

**Journey log**
- 用户场景是服务器部署，runbook 必须同时覆盖 Windows 本机与 Linux/Docker 服务器。
- 最易混的是两类密钥：供应商 key 与兰台自身 REST key，步骤里单独列表辨析。
- history scrub 作废 key 后已无害，故标为可选且放在验收之后，避免 force-push 阻断止血。

## [S1] Problem

`.env` 中的硅基流动 `OPENAI_API_KEY` 曾提交进 git 历史（`2966b3d`），并可能已推送到 GitHub `JIUSHIQINGSHAN/Lantai`。当前工作区与服务器部署仍在使用同一密钥。需要一份可照做的轮换步骤，覆盖：供应商作废重建 → 本机 `.env` → 服务器 `.env` → 重启验收 → 可选 history 清理。

## [S2] Design

新增 `docs/key-rotation-runbook.md`，并在 `docs/ops-runbook.md` 增加交叉引用。

Runbook 必须区分两类密钥（易混）：
- `OPENAI_API_KEY`：硅基流动等 OpenAI 兼容端点，**本次轮换对象**
- `API_KEY`：兰台自身 REST 鉴权（`X-API-Key`），与供应商无关

步骤分五阶段，每阶段有可观察验收标准；不写入任何真实密钥明文。

## [S3] Out of Scope

- 实际创建硅基流动密钥（仅维护者控制台可操作）
- 自动 force-push / git-filter-repo 执行（文档给出命令，执行需人工确认）
- 轮换 `API_KEY`（兰台 REST 密钥）——若同样泄露另开流程

## Tasks

- [x] T1: 写 `docs/key-rotation-runbook.md` 五阶段步骤 — acceptance: 覆盖控制台/本机/服务器/验证/可选 history；含密钥类型辨析 (covers: S2)
- [x] T2: `ops-runbook.md` 增加链接 — acceptance: 运维手册能找到轮换 runbook (covers: S2; depends: T1)
- [x] T3: compose 规格收口并提交 — acceptance: status=delivered，commits 填齐 (covers: S2; depends: T1,T2)
