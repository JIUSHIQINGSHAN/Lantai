# Iteration 04 — 平台/框架原生记忆一手核验（2026-09-19）

## 类型
survey（平台原生批）

## 吸收的关键事实
- **ChatGPT Memory + Dreaming V3**（openai.com，2026-06-04）：Dreaming=后台参考聊天历史自动合成记忆（2025-04 V0→2026-06 V3），Free 用户计算量降约 5 倍；三大评测目标=延续上下文/遵循偏好/随时间保持更新（"将去新加坡"→"已去过"）；memory summary 页可查看/编辑/设置提及指令。
- **Claude Memory 产品级**（support.claude.com 一手）：2025-09 上线记忆摘要+搜索过往聊天（引用回链）+incognito 排除；**2026-08-06** 记忆跨 chat+Cowork 云端共享，Topics 视图逐条编辑/删除，敏感主题（健康/信仰）默认不入库，消费级默认开启/企业默认关闭；有导入/导出通道。
- **Cursor Memories**（官方论坛一手）：对话自动生成记忆（可批准），云端账户存储；隐私争议（需共享代码、隐私模式不可用、作用域混乱）；**自 2.1.17 起功能被移除**，提供导出转 Rules(.mdc)。
- **OpenAI Agents SDK Sessions**（官方文档一手）：会话历史自动前置/自动存储；统一 Session 协议（get_items/add_items/pop_item/clear_session）可插拔后端（SQLite/Redis/SQLAlchemy/Mongo/Dapr）；EncryptedSession（透明加密+TTL）与 CompactionSession 包装器；定位=客户端会话记忆，非长期记忆。

## 新增系统
landscape.json：12 → **16**（chatgpt_memory / claude_memory / cursor_memories / openai_agents_sessions，全部 verified）

## 评分变化
新增四系统加权总分：2.23 / 2.12 / 1.34 / 1.55。兰台未动（无内部新证据）。

## 决策
- D10：平台原生记忆已把「用户可见可编辑 + 导入导出 + 敏感主题默认排除」做成标配。兰台的治理叙事必须**超过**这个基线（可审计的机器级 provenance vs 平台的页面级摘要），否则对外无感。
- D11：Cursor Memories 被移除是「客户端内建记忆脆弱性」的直接实证；独立记忆层 + 可导出 + 跨宿主是真实需求（与 agentmemory 32+ 客户端佐证互洽）。
- D12：Agents SDK Sessions 印证兰台「底本（会话快照）/ 札记（工作暂存）/ 长期记忆」三层分层的正确性——会话层与长期记忆应保持清晰边界。解决 Q4。

## 开放问题
- Q6：兰台是否有面向用户的「记忆摘要页」（对齐 memory summary/Topics 交互基线）？——待查（悬镜可能部分覆盖）。

## 覆盖
systems=16 papers=32 new_primary_sources=7 score_delta_sum=0(兰台) roadmap_churn=0
