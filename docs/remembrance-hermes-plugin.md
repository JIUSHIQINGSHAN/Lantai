# Hermes 插件：记忆注入 + 对话自动写入

插件源码维护在仓库 `hermes-plugin/remembrance-hook/`（版本化、可测试），
部署到 Hermes home 的 `plugins/remembrance-hook/`。

## 功能（v1.1.0）

| 钩子 | 作用 |
|---|---|
| `pre_llm_call` | 每轮对话前检索 Remembrance 记忆注入 user message；同时把 user_message 累积到会话缓冲 |
| `on_session_end` | 每轮对话结束触发（桌面版/CLI 通用），把缓冲 flush 给对话写通道 → `ingest_dialogue` |

对话写通道语义（`remembrance/ingestion/dialogue.py`）：
- 「记住：X」/ 自我声明 / 偏好表达 → fastpath 直通
- 闲聊（过短/社交结束语）→ 候选进待审队列（不静默丢弃）
- LLM 提取低置信度 / 上游失败 → 兜底候选入队（不丢数据）

安全边界（插件绝不阻塞/搞崩 Hermes）：
- 子进程启动失败/失活 → 静默降级
- 注入 5s 超时、对话写入 30s 超时（含 LLM 提取）
- 会话缓冲有界：200 条 / 20 万字符，超出丢最旧

## 部署

```bash
# 默认 Hermes home = C:/Users/Asus/AppData/Local/hermes
python scripts/install_hermes_plugin.py
# 自定义路径：
python scripts/install_hermes_plugin.py --hermes-home <path>
```

脚本行为：备份现有插件到扫描目录外的 `plugins-backup/remembrance-hook-YYYYMMDD`（不删除，
备份内 `plugin.yaml` 改名为 `plugin.yaml.disabled`，避免被插件加载器当作同名候选扫描）→
复制新文件 → 自检无同名冲突 → 提示重启 Hermes。

## 验证

1. 重启 Hermes 桌面版，日志出现 `on_session_end 对话写入已注册`
2. 与 Hermes 聊一轮（说几句有价值的话），结束后：
   - `GET /candidates/pending` 应出现候选（或已直通进记忆库）
   - `GET /usage` 每日新增数增长
3. 回滚：删除 `plugins/remembrance-hook`，把 `plugins-backup/remembrance-hook-YYYYMMDD`
   移回 `plugins/remembrance-hook`，并把备份内 `plugin.yaml.disabled` 改回 `plugin.yaml`，然后重启

## 开发

- 插件不 import remembrance/Hermes 任何库（`register(ctx)` 注入），可独立测试：
  `tests/test_hermes_plugin.py`（缓冲/flush/回调注册，mock 子进程交互）
- serve 子进程协议扩展：`{"type": "dialogue", "text": ...}` →
  `{"ok": true, ...ingest_dialogue 结果}`；缺省 type 保持注入语义（向后兼容）
