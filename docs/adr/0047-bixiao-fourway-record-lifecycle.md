# ADR-0047: 笔削——撤回/删除四分法（纠错/撤回/归档/删除语义分离）

**日期**: 2026-09-19
**状态**: Accepted
**决策者**: 大哥
**来源**: 记忆系统全景调研 iter-12 D23（`docs/research/memory-landscape-2026-09/iterations/iter-12.md`）+ 方向报告主线 B（`docs/research/agent-memory-development-directions-2026-09.md`）

---

## 背景

兰台治理面在调研中被评为全场独占（人工闸门+回滚+注入围栏闭环），但删除语义是明确短板：

- 只有裸删除（`DELETE /terminal/memory/{id}`，SQLite+FTS+Chroma 同步失败静默吞、无审计）；
- 沉潜（ADR-0036）会自动把深度衰减记忆转 `status="archived"`，但无手动归档/恢复端点；
- 「这条主张错了/不安全，永远别再用它」的**撤回**语义完全缺失；
- 纠错与裸更新不分：`PATCH /terminal/memory` 改文不留版本痕迹。

研究结论（iter-12）：平台已把页面级用户控制做成标配，兰台的治理优势必须以两条方式兑现——机器级可审计、四分删除语义落地（撤回传播到 FTS/Chroma/缓存/宿主副本）。

## 决策

四种操作语义分离，伞名**笔削**（《史记·孔子世家》「笔则笔，削则削」），API 用通俗动词：

| 操作 | 语义 | 可逆性 | 内容留存 | 覆盖面 |
|---|---|---|---|---|
| 纠错 correct | 就地改正并保留版本历史 | — | 旧文留 `provenance.corrections` | SQLite + FTS + Chroma 重同步 |
| 撤回 retract | 停止使用某主张，全检索面禁用 | 不可自动复活（unretract 仅 admin） | 行保留供审计 | SQLite（`status="retracted"`）+ FTS 移除 + Chroma 移除 |
| 归档 archive | 可逆退出常规检索 | 可逆（unarchive） | 不动 | SQLite（`status="archived"`），FTS/Chroma 保留靠 SQL 过滤 |
| 删除 delete | 清除内容（隐私删除） | 不可逆 | 正文不留，仅留无正文审计 | SQLite + FTS + Chroma + 审计 |

验收口径（D23）：**确定性用例「撤回后禁用命中=0」**——retract 后 hybrid_search、`memory_fts`、向量库三面均 0 命中。

配套约束：

1. **不复活**：任何 worker/生命周期路径不得把 `status="retracted"` 翻回 active（沉潜只选 active；promote 只收 candidate——现状即满足，落锚测试固化）。
2. **同步失败不静默**：FTS/Chroma 同步结果如实进响应（`fts_removed` / `vector_removed` / `warnings`）与日志。
3. **审计不含正文**：新表 `MemoryAuditEvent` 只记 id/action/actor/reason/content_hash/content_len/version_at，六操作（correct/retract/unretract/archive/unarchive/delete）统一走单一审计写入。审计行不含主体冗余列（tenant/user）——delete 后源行已不存在，按主体导出审计需经 memory_id 关联或未来加列（D22 已知限制）。
4. 归档复用沉潜既有 `status="archived"` 值，不另造状态；仅 active 可入（candidate 不得绕晋升闸门、retracted 不得降级），archived 幂等重入；检索出口依赖 hybrid 既有 `MemoryItem.status == "active"` SQL 谓词。
5. 纠错与裸更新并存：PATCH 是无痕快改，correct 是留痕纠错；选择指引见票据 04。
6. **FTS 失败策略双轨（有意区分）**：改文类操作（PATCH 更新、correct）FTS 同事务强一致——失败随事务回滚，杜绝「可命中旧文」的脏索引；停用类操作（retract）与删除 FTS 失败降级为如实上报——SQL status 过滤面权威兜底，宁 miss 不脏写。

## 边界（如实声明，不夸口）

- **摘要（digest）**：现状为纯统计日报（`docs/memory-digest/`），不含记忆正文——天然覆盖，无需钩子。
- **缓存**：当前代码无检索缓存层；未来引入缓存必须挂钩 retract/delete，否则撤回语义破防。
- **宿主副本**：已注入宿主会话的副本服务端无法追溯清除；缓解=樊篱围栏（P0 票03 落地的 `lantai/llm/fence.py`）+ 注入回执链 + 下次注入刷新。不承诺「撤回即从宿主上下文消失」。
- **MCP 面**：现状无删除类 MCP 工具，本波只做 REST API，MCP 映射另票。
- **按范围删除**（by user/source 批量）：另票。
- **派生物**：撤回 v1 只处理主张本身（FTS/Chroma/SQL 三面）；无其他支持来源的派生物（场景摘要/边缘）不自动追溯处理，记入已知限制。

## 后果

- 撤回成为可证明的治理承诺（三面 0 命中门禁测试），补齐 Governed Shared Memory 四原语中的 temporal supersession 之外的「policy propagation」本地实现。
- 审计表为后续「导出机器级审计」（调研 D22 交付物）提供数据基础。
- `merge_memories` 的 source 删除暂不升级（保持现状），后续随范围删除一并处理。
