# 04 - RECALL 追忆漏斗控制台（借鉴 aiduMEI v18.2 控制台 RECALL 面板）

Status: resolved
Type: task
Blocked by: (none)

## 目标

aiduMEI v18.2 自带零依赖 Web 控制台（PULSE/VAULT/MAP/RECALL/EVOLVE/SETTINGS），
其中 RECALL 面板把「它凭什么想起这条」可视化：候选池→点火→去重→衰减→最终，
每步耗时与命中数全可见。兰台已具备 search_trace 数据结构，缺的只是可视化。

## 范围

- `lantai/api/routes_ui.py`（新）：`GET /ui/recall` 返回零依赖静态页（内联
  CSS/JS，无 node/打包），`GET /ui` 重定向到面板；公开挂载（与 /health 同级，
  页面不含数据，检索仍走受保护的 POST /search）。
- 页面行为：输入查询 → `POST /search?trace=true` → 渲染闸门裁决、召回漏斗
  （意图→向量→衰减→(重排)→最终/FTS兜底，条宽按耗时比例）、结果列表
  （score + memory_type + lane + 内容）；API Key 可选（localStorage 记忆，
  非回环部署时填 X-API-Key）。

## 验收

1. GET /ui/recall 200 + text/html，含面板标记；GET /ui 307 → /ui/recall。
2. /search?trace=true 的 trace 契约（step/elapsed_ms/candidate_count/
   score_range）端到端可渲染：闸门放行时出漏斗步集，拦截时显示闸门裁决。
3. 全量测试无回归。

## Answer（2026-08-11 已实现）

- `lantai/api/routes_ui.py`：_UI_HTML 单文件页面（中文本地化，步骤标签映射
  intent/vector_search/decay_filter/rerank/final/fallback_fts），DOM 构建
  防注入（textContent，不用 innerHTML 拼内容）；/ui 307 重定向。
- 注册：`lantai/api/__init__.py` + `api_server.py` 公开 include_router。
- 测试：`tests/test_ui_recall.py` 2 例冒烟（页面可达含标记；/ui 重定向
  follow_redirects=False 断言 307）。
- 端到端验证：TestClient 探针确认闸门放行时 trace 步集返回（intent、
  vector_search…），拦截时返回 gate 裁决——与页面渲染契约一致。
- 探针发现：未加载 conftest 的裸 TestClient 会启动真实调度器（后台线程对
  真实库做 LLM 调用拖慢进程）——正是票据 03 已修的污染源，测试环境统一
  由 conftest 防护。

验收对照：1. ✅ 2. ✅（契约探针）3. ✅ 全量 5xx passed
