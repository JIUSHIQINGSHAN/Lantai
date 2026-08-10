# 交接文档：Hermes 自举安装 Remembrance 记忆系统

> **给 Hermes（爱马仕）的任务**：请按本文档逐步完成兰台记忆（Lantai）记忆系统的接入安装。
> 文档由小弟（DeepSeek-V4-Flash）编写，代码/脚本层面已全部就绪，你只负责**执行配置 + 验证 + 回传结果**。
>
> **硬性规则（违反即中止，不可自由发挥）：**
> 1. 除 Step 0 明确允许的 pip 操作外，**禁止安装/升级/删除任何 Python 包**。
> 2. **禁止删除任何旧数据目录或旧文件**——迁移只复制，不删除。
> 3. 每步执行后必须核对预期输出；**任何不确定立刻停下，不要猜**，把疑问原样回传给人。
> 4. 禁止修改 Remembrance 的源代码（scripts/ 与 remembrance/ 下的 .py 都不许动）。

---

## Step 0 · 环境修复：hermes venv 的 OpenTelemetry 版本错配

**背景**：hermes venv 中 `opentelemetry-exporter-otlp-proto-grpc==1.44.0` 与 `opentelemetry-exporter-otlp-proto-common==1.39.1` 版本不匹配，导致 chromadb 一 import 即崩溃（`ModuleNotFoundError: _exporter_metrics`）。Remembrance 的 MCP server 依赖 chromadb，不修则 MCP 工具无法启动。

**修复步骤**（备份优先，绝不用 pip 卸载）：

```bash
cd C:/Users/Asus/AppData/Local/hermes/hermes-agent/venv/Lib/site-packages

# 1. 改名备份旧 otel proto/common（含 dist-info；改名=备份，不删除）
mv opentelemetry/proto opentelemetry/proto.bak.139
mv opentelemetry/exporter/otlp/proto/common opentelemetry/exporter/otlp/proto/common.bak.139
mv opentelemetry_proto-1.39.1.dist-info opentelemetry_proto-1.39.1.dist-info.bak
mv opentelemetry_exporter_otlp_proto_common-1.39.1.dist-info opentelemetry_exporter_otlp_proto_common-1.39.1.dist-info.bak

# 2. pip 全新安装匹配版本（1.44.0，与 grpc 对齐；旧包已移走，无 safe-delete 冲突）
cd C:/Users/Asus/AppData/Local/hermes/hermes-agent/venv/Scripts
./python.exe -m pip install "opentelemetry-exporter-otlp-proto-common==1.44.0" "opentelemetry-proto==1.44.0"

# 3. 验证（预期：无 ModuleNotFoundError）
./python.exe -c "import chromadb; print('chromadb OK')"
```

**预期输出**：`chromadb OK`。
**失败处理**：若第 3 步仍报 `_exporter_metrics` 缺失，把 `.bak.139` 目录移回原位（`mv xxx.bak.139 xxx`）恢复原状，然后停下回传，不要继续。
**风险备注**：升级会改变 hermes venv 的 otel proto/common 版本（1.39 → 1.44）。`.bak.139` 保留不删，若 Hermes 本体出现异常，用 `mv` 移回即可回滚。
**验收**：输出 `chromadb OK` 才可继续。

---

## Step 1 · 数据迁移（REMEMBRANCE_HOME 独立）

**背景**：记忆数据目前与代码同目录（`C:/Users/Asus/Desktop/记忆/`），长期记忆不该躺在代码工作区里。迁移到独立数据目录。

```bash
# 建议目标目录（若不存在会自动创建）
cd C:/Users/Asus/Desktop/记忆
C:/Users/Asus/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe scripts/migrate_home.py --target "E:/c/Users/Asus/AppData/Local/remembrance-data"
```

**预期输出**：5 步全部 `[OK]`，结尾提示"旧目录未删除，请确认后手动清理"。
**硬性要求**：迁移脚本会自动 ①SQLite online backup（一致性快照 + integrity_check）②复制 .chromadb ③写用户级环境变量 `REMEMBRANCE_HOME` ④写项目根 .env。**你不得手动移动任何文件**。
**验收**：脚本输出含 `[OK] DB 备份完成` 与 `[5/5]`。

---

## Step 2 · Hermes 配置（config.yaml）

**文件**：`E:/c/Users/Asus/AppData/Local/hermes/config.yaml`

在 `mcp_servers:` 段下追加（保持现有缩进风格，2 空格）：

```yaml
mcp_servers:
  remembrance:
    command: C:/Users/Asus/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe
    args:
      - C:/Users/Asus/Desktop/记忆/scripts/mcp_server.py
    connect_timeout: 30.0
    enabled: true
```

**注意**：mcp_server.py 已内置 sys.path 处理，子进程无论 cwd 都能 import remembrance；REMEMBRANCE_HOME 已设用户级环境变量，子进程继承后自动生效。

---

## Step 3 · Shell Hook 注册（读通道）

Hermes 的 shell hooks 从 cli-config.yaml 的 `hooks:` 块读取（若该文件不存在，创建它；若已存在，追加 `hooks:` 段）：

```yaml
hooks:
  pre_llm_call:
    command: "C:/Users/Asus/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe C:/Users/Asus/Desktop/记忆/scripts/shell_hook.py"
    timeout: 3
hooks_auto_accept: true
```

**说明**：
- `command` 经 shlex 拆分，双引号防止路径歧义；两条路径均无空格，安全。
- `hooks_auto_accept: true` 跳过首次使用同意弹窗（等价于 `HERMES_ACCEPT_HOOKS=1`）。
- 协议：Hermes pre_llm_call 发 stdin JSON → shell_hook.py 返回 `{"context": "..."}` 注入相关记忆；与 Hermes 原生协议逐字兼容。
- 若 Hermes 实际读取的配置文件与上述不同，**停下来**，把实际配置文件名回传给人，不要猜测。

---

## Step 4 · 自检（验证闭环，必须执行）

```bash
cd C:/Users/Asus/Desktop/记忆
C:/Users/Asus/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe scripts/verify_remembrance.py
```

**预期**：8 项检查全 PASS（REMEMBRANCE_HOME 已设置、DB 可读、MCP/Hook 可导入、Hook 契约、/health）。
**若有 FAIL**：把 FAIL 项的完整输出回传给人，不要自行"修复"。

---

## Step 5 · 回传结果（必须）

向人回传以下内容（可直接粘贴）：

```
1. chromadb 验证: PASS/FAIL + 输出
2. 迁移结果: 新目录路径 + 脚本最后 5 行
3. config.yaml 追加结果: 粘贴你写入的 remembrance 段
4. hooks 配置结果: 粘贴你写入的 hooks 段
5. 自检结果: 8 项 PASS/FAIL 清单
6. MCP 工具列表: hermes 里 tools/list 是否包含 search/add/feedback
7. 你遇到的任何不确定点（原样列出，不要自行处理）
```

---

## 交付物与前置（已就绪，勿重复制作）

| 文件 | 用途 |
|---|---|
| `scripts/mcp_server.py` | MCP 服务（已补 sys.path） |
| `scripts/shell_hook.py` | Shell Hook 注入（协议兼容） |
| `scripts/migrate_home.py` | 数据迁移（备份优先、不删旧） |
| `scripts/verify_remembrance.py` | 接入自检（8 项） |
| `remembrance/` 全套 | 记忆引擎（勿动） |

## 完成标准

1. `chromadb OK`
2. `REMEMBRANCE_HOME` 指向新目录且 DB 可读
3. Hermes MCP 工具列表含 `search / add / feedback`
4. pre_llm_call 注入返回 `{"context": ...}`（可为空串，但不能是错误）
5. verify_remembrance.py 全 PASS
6. 回传完整结果

---

*编写：小弟（DeepSeek-V4-Flash）· 2026-08-03 · 交办：Hermes（爱马仕）自举安装*
