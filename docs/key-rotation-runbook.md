# 密钥轮换 Runbook

> 适用：硅基流动（或任意 OpenAI 兼容端点）的 `OPENAI_API_KEY` 泄露后紧急轮换。
> 本机 Windows 工作区 + 远端服务器部署双环境。

## 先分清两类密钥

| 环境变量 | 是谁的密钥 | 泄露后果 | 本次是否轮换 |
|----------|------------|----------|--------------|
| `OPENAI_API_KEY` | 硅基流动等 LLM 端点 | 别人刷你的模型额度 | **是** |
| `API_KEY` | 兰台 REST（`X-API-Key`） | 别人读写你的记忆库 | 否（若也曾入库再单独轮换） |
| `RERANKER_API_KEY` | 精排端点（可空回退 OPENAI） | 同 OPENAI | 若单独配置过则一并轮换 |

当前泄露点：commit `2966b3d` 提交过 `.env`，`a7a4db0` 已从 HEAD 删除，但 **git 历史与 GitHub 上仍可还原**。仓库：`https://github.com/JIUSHIQINGSHAN/Lantai`。

---

## 阶段 0 — 准备（2 分钟）

在**有浏览器登录硅基流动**的机器上操作。准备记事本暂存新 key（不要发聊天/截图给任何人）。

确认本机与服务器能访问：

```powershell
# 本机（Windows，仓库根）
cd C:\Users\Asus\Desktop\记忆
Select-String -Path .env -Pattern 'OPENAI_API_KEY'
```

```bash
# 服务器（路径按实际部署；常见 /opt/lantai/.env 或 $LANTAI_HOME 同级）
grep -n 'OPENAI_API_KEY' /path/to/lantai/.env
```

记下旧 key 前缀（如 `sk-ykhksn…`）以便控制台对照，**不要**把完整 key 写进工单。

---

## 阶段 1 — 硅基流动控制台：作废旧 key + 新建

1. 打开 https://cloud.siliconflow.cn/ → 登录 → **API 密钥**（API Keys）。
2. 在列表中找到与旧前缀匹配的密钥 → **删除 / 作废**（Delete / Revoke）。
3. **新建 API Key**（Create API Key），名称建议 `lantai-prod-20260831`（含日期，便于下次轮换）。
4. 复制完整新 key（通常只显示一次）。

**验收**：旧 key 在控制台已消失；新 key 已复制到本机记事本。

> 也可先建新 key、验证通过后再删旧 key（更稳，有短暂双活窗口）。泄露场景建议**先删旧再建新**，立刻止血。

---

## 阶段 2 — 本机 Windows：写入 `.env` 并验证

```powershell
cd C:\Users\Asus\Desktop\记忆

# 1) 只替换 OPENAI_API_KEY 那一行（勿动 API_KEY / LANTAI_HOME）
# 用编辑器打开 .env，将 OPENAI_API_KEY= 后面换成新 key，保存。

# 2) 确认写入成功（应显示新前缀，完整值勿回显到共享屏幕）
Select-String -Path .env -Pattern '^OPENAI_API_KEY='

# 3) 确认 .env 仍未被 git 跟踪
git check-ignore -v .env
git ls-files .env   # 应无输出
```

**验收**：`OPENAI_API_KEY` 为新值；`git ls-files .env` 为空。

若本机有常驻 `lantai-server` / Docker，重启进程使配置生效：

```powershell
# 源码方式：重启你的 lantai-server / uvicorn 进程
# Docker：改挂载的 .env 后
docker restart <container>
```

---

## 阶段 3 — 服务器：同步新 key 并重启

以下按常见 Linux 部署；Docker / systemd 二选一。

### 3a. 直接改服务器 `.env`

```bash
# SSH 到服务器
ssh user@your-server

# 定位 .env（若用 LANTAI_HOME，配置文件可能在服务目录，数据在 LANTAI_HOME）
sudo grep -n 'OPENAI_API_KEY' /opt/lantai/.env   # 路径按实际

# 备份后编辑
sudo cp /opt/lantai/.env /opt/lantai/.env.bak.$(date +%Y%m%d)
sudo nano /opt/lantai/.env
# 只改 OPENAI_API_KEY=新key，保存

# 重启服务（按你的启动方式）
sudo systemctl restart lantai
# 或
sudo docker restart lantai
# 或
sudo systemctl restart lantai-api
```

### 3b. 环境变量注入（无 .env 文件时）

若用 systemd `Environment=` 或 compose `environment:`，改的是部署清单而不是 `.env`，改完同样 `daemon-reload` + `restart`。

**验收**：`systemctl status lantai`（或容器）为 running；旧进程已退出。

---

## 阶段 4 — 端到端验收（两边都要做）

```bash
# 1) 健康检查（不要求 LLM，只证明服务活着）
curl -s http://127.0.0.1:8767/health

# 2) 触发一次真实 LLM/embedding 路径：加一条记忆
curl -s -X POST http://127.0.0.1:8767/add \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <你的兰台 API_KEY，若已配置>" \
  -d '{"title":"key-rotate-smoke","content":"密钥轮换后冒烟测试记忆条目","lane":"fact"}'

# 3) 检索仍可用
curl -s -X POST http://127.0.0.1:8767/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <同上>" \
  -d '{"query":"密钥轮换","top_k":3}'
```

也可开司天：`http://<host>:8767/ui` → 侧边栏「司天监控」，看 5xx 与 `llm_key_missing` 告警是否消失。

**验收**：
- `/add` 返回 200（或成功体）
- 日志无 `401 Unauthorized` / `Invalid API key` 来自硅基流动
- 旧 key 在控制台已不可用（可选：用旧 key 调 `/v1/models` 应 401）

---

## 阶段 5 — 可选：清除 git 历史中的旧 `.env`

旧 key **作废后**历史泄露已无害，可不清理。若仍想物理抹除（仓库已推 GitHub 时需 force-push，协作者需重克隆）：

```bash
# 需要 git-filter-repo（https://github.com/newren/git-filter-repo）
# 在本地 clone 上操作，不要在服务器生产目录操作

git clone https://github.com/JIUSHIQINGSHAN/Lantai.git lantai-scrub
cd lantai-scrub
git filter-repo --invert-paths --path .env --path remembrance.db

# 强推所有分支/标签（破坏性！确认协作者知情）
git push --force --mirror origin
```

**警告**：force-push 会改写远端历史；任何未重置的克隆在 pull 时会冲突。团队仓库需全员重克隆。**先完成阶段 1–4，再考虑本阶段。**

---

## 轮换后检查清单

- [ ] 硅基流动旧 key 已删除
- [ ] 本机 `.env` `OPENAI_API_KEY` 已更新且未入 git
- [ ] 服务器 `.env` / 环境变量已更新
- [ ] 本机与服务器 `/add` + `/search` 冒烟通过
- [ ] 司天无 `llm_key_missing` / 无供应商 401
- [ ] （可选）history 已 scrub 并 force-push
- [ ] （建议）设置日历提醒：90 天后再次轮换

## 常见坑

1. **改了本机忘了服务器**（或反过来）——两边都要改并各自验收。
2. **改错变量**——把新 key 写进 `API_KEY`（兰台 REST）而不是 `OPENAI_API_KEY`。
3. **只改 shell export 不改 `.env`**——兰台用 pydantic-settings 读 `.env`，进程重启后会以文件为准。
4. **`.env` 被误提交**——每次提交前 `git status`；`.gitignore` 已含 `.env`，但 `git add -f` 可绕过。
5. **新 key 含 `#` 或空格**——`.env` 解析可能截断；值不要加引号外的多余字符。
