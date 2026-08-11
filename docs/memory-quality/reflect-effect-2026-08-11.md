# 反思模块效果验证报告（superseded 残留率归零）

> 生成时间：2026-08-11 18:53 中国标准时间
> 场景：中文评测集 superseded 两例（公司域名 / API 密钥），新值 supersedes 旧值，
> 旧值仍 active 可召回（基线残留率 1.0）。
> 方法：LLM 按测试纪律 mock（curator 返回 deprecate 提案、rejecter 返回 accept/low），
> DB/FTS5/健康扫描/提案落库/自动应用全部真实执行；检索以 active 过滤后的
> 确定性结果集模拟（hybrid_search active 过滤见 hybrid.py L161/L239/L379）。

## 反思前（基线）

| 指标 | 值 |
|---|---|
| superseded_active（健康扫描） | 2 |
| superseded_residual_rate | 1.0 |
| superseded_order_accuracy | 1.0 |

## 反思后

| 指标 | 值 |
|---|---|
| superseded_active（健康扫描） | 0 |
| superseded_residual_rate | 0.0 |
| superseded_order_accuracy | 1.0 |
| 提案数 / 自动应用 / 待审 / 丢弃 | 2 / 2 / 0 / 0 |

| 用例 | 提案 | 旧值 | 处理后状态 |
|---|---|---|---|
| 公司域名 | 0.9(deprecate) | 公司域名是 example.com | archived |
| API 密钥 | 0.9(deprecate) | API 密钥存储在 config.py | archived |

## 结论

**通过**：反思自动应用 deprecate 后，被取代旧值退出 active 集合，残留率从 1.0 归零；新值保持 active，supersedes 边保留，健康快照 superseded_active 2 → 0（闭环自证）。

> 诚实标注：本验证不跑混合检索链路，残留率归零是 active 过滤的直接推论；
> 若未来检索改为不依赖 status 过滤，需以真实链路复测。
