# v0.9 — MAP 星图面板（作者六面板最后一块）

来源：aiduMEI v18.3.0 控制台 MAP 面板（ECharts 力导向：核心/知识域/分类/实体四类
节点）。兰台已有 PULSE/VAULT/RECALL/EVOLVE 四面板，MAP 是唯一缺口。

## 窄版差异（不照搬）

- 不用 ECharts CDN（兰台零依赖）：`/ui/map` 内联 HTML + SVG 放射布局（按 lane
  扇区 + scene 成员聚簇 + MemoryEdge 关系线），无外部请求。
- 数据源用兰台自己的 MemoryEdge（supports/refines/contradicts/supersedes）+
  scene 归属；两类节点：active 记忆（圆点，按 lane 分区）+ 参与边的来源文档
  RawDocument（外环矩形，点击开原文 URL）——出处可溯，不做作者那套四类节点。
- 只读展示，不新增写路径。

## 票据

- 01-map-panel：`ops/graph.py::build_graph` 纯函数 + `GET /graph` + `/ui/map` 面板
  + MCP `graph_view`（工具 38 → 39）

## 明确不吸收（本轮审阅）

- layer1_selfcheck 容量 >80% 自动合并：自动合并违背「宁 miss 不脏写」（走提案
  人工裁决）；只读容量信息价值低，跳过。
- instinct_graduation 自动毕业 + 删除原始：v0.7 crystal 已覆盖（候选 + 人工
  审核 + 保留溯源），作者版「自动升格 + 删原文」不吸收。
