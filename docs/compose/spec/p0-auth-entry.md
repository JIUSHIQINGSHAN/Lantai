---
feature: p0-auth-entry
status: in-progress
updated: 2026-08-31
branch: fix/p0-auth-entry
commits: 
---

# P0 鉴权统一与启动入口修复

## Report

## [S1] Problem

1. **鉴权双轨断裂**：业务路由挂的是 `get_current_user`（Bearer + 库内 `api_keys`）；环境变量 `API_KEY` / `verify_api_key` / `X-API-Key` 从未挂到任何路由。库内无 key 时无凭证即 DEV MODE 全放行，Docker 非回环部署若空库则零鉴权可写记忆。
2. **启动入口不存在**：`Dockerfile` `COPY api_server.py` / `CMD python api_server.py` 与 README 快速开始均引用不存在的文件；`pyproject.toml` 声明 `lantai-server = lantai.api.app:main` 但 `app.py` 无 `main()`。镜像构建与 `lantai-server` 控制台脚本均不可用。

## [S2] Design

### 鉴权：双密钥并存 + 非回环禁 DEV MODE

在 `get_current_user`（`lantai/core/auth.py`）统一入口，按序尝试：

| 顺序 | 条件 | 结果 Principal |
|------|------|----------------|
| A | `X-API-Key` 与 `settings.API_KEY` 恒时比较一致 | `user_id="api_key"`，`role="admin"`，全默认 lane |
| B | `Authorization: Bearer <key>` 命中库内 `ApiKey` 且 `is_active` | 原行为：库内 user_id + allowed_lanes |
| C | 回环 **且** `API_KEY` 为空 **且** 库内无任何 `ApiKey` 行 | DEV MODE：`user_id="default"`，全默认 lane（本机开发） |
| 否 | — | 401：缺凭证或无效 |

规则细节：
- 若 `settings.API_KEY` 已配置：A 不通过时仍允许 B；A/B 均失败 → 401（**不允许** C）。
- 若绑定非回环：**永不**进入 C（`assert_secure_binding` 已强制配置 API_KEY，请求路径再挡一层）。
- `verify_api_key` 保留（兼容旧调用方/测试），但业务路由不单独挂它；语义与 A 一致。
- 公共端点（`/health`、`/ui`、`/admin` 页面）不经过此依赖；`/admin/api/*` 仍走 `require_admin`。

### 启动入口

1. 在 `lantai/api/app.py` 增加 `def main():` 读取 settings 并 `uvicorn.run(app, host=..., port=...)`，满足 `lantai-server` 入口。
2. 新增根目录 `api_server.py` 薄 shim：`from lantai.api.app import main; if __name__ == "__main__": main()`（兼容旧文档/习惯）。
3. `Dockerfile`：保留 `COPY api_server.py`（shim 入库后存在）；CMD 可改为 `["lantai-server"]` 优先使用 console script（更干净），shim 作 fallback 文档一致。
4. `README.md` 快速开始：`python scripts/init_db.py` + `lantai-server`（或 `python api_server.py`），删除对不存在文件的暗示。

### 测试契约

扩展 `tests/test_auth.py`：
1. 设置 `API_KEY` 后，带正确 `X-API-Key` 访问 `/add` → 200（或业务成功码）。
2. 设置 `API_KEY` 后，无任何凭证 → 401。
3. 设置 `API_KEY` 后，错误 `X-API-Key` 且无 Bearer → 401。
4. 设置 `API_KEY` 且库内有 key 时，正确 Bearer → 仍可用（双轨）。
5. 非回环 + 空库 + 无凭证 → 401（不可 DEV MODE）。
6. `main` 可导入；`api_server` 模块存在且导出 main。

## [S3] Out of Scope

- SiliconFlow 密钥轮换与 git history purge（维护者人工）。
- `/admin` 角色真正可赋值（P1：`require_admin` 恒 403 问题）。
- 多 worker 拒绝启动、`_param_override` 清理、routes_ui 拆分等 P1。
- OAuth / 多租户完整模型。

## Tasks

- [ ] T1: 重写 `get_current_user` 双轨逻辑 + 导出 DEV 判定纯函数 — acceptance: 上述规则 A/B/C 可被单测直接覆盖；非回环空库无凭证 401 (covers: S2)
- [ ] T2: 扩展 `tests/test_auth.py` 覆盖双密钥与非回环禁 DEV — acceptance: 新测试在改前失败（若可复现）、改后通过 (covers: S2; depends: T1)
- [ ] T3: 为 `app.py` 增加 `main()`，新增 `api_server.py` shim — acceptance: `from lantai.api.app import main` 成功；`python -c "import api_server"` 成功 (covers: S2)
- [ ] T4: 修正 Dockerfile CMD 与 README 启动步骤 — acceptance: 文档不再引用缺失逻辑；Dockerfile CMD 使用已存在入口 (covers: S2; depends: T3)
- [ ] T5: 全量相关测试 + ruff — acceptance: `pytest tests/test_auth.py -q` 与 `ruff check` 通过 (covers: S2; depends: T1,T2,T3,T4)
