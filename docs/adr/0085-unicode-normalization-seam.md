# ADR-0085: 归一缝——只归一派生数据，不改写用户正文

**日期**: 2026-11-09
**状态**: Accepted
**决策者**: Claude（票据 `.scratch/v0.68-next-window-2026-11/issues/45-unicode-normalization-seam-absent.md`，P2）
**来源**: v0.68 立案轮 7 深度巡检；全仓 `unicodedata` 仅检测闸两处瞬态 NFKC（獬豸「只检测不改写」），落库/索引/查询/去重面全程零归一。

---

## 1. 背景与痛点

SQLite trigram 分词按精确码位切 3 字 gram，Python `\w` 保留全角字母——双向都是**确定性 miss**，不是概率性降级：

1. **索引与查询零归一**：正文「ｄｅｃｉｓｉｏｎ」（全角）对查询「decision」零召回，反向同构；NFD 形态（macOS 文件名/某些 IME）同理。
2. **零宽/双向控制符无清洗点**：正文一个 ZWSP（网页粘贴常态）把「机器学习」的 trigram 邻接撕开，查询 gram 零命中；RTL override（U+202E）透传宿主终端可致视觉伪装。
3. **案牍去重键 casefold 不含 NFKC**：`casefold` 把 Ａ(U+FF21) 折成 ａ(U+FF41) 而非 a——全角标题变体生成不同组键，待审队列出双份工作项。
4. **登记项**：反思聚类签名（`evolution._get_signature` 的 `.lower()`）与检索指纹（`retrieval_log._norm_hash`）同病。

## 2. 决策

### 2.1 单一归一缝，不在各消费点零散归一

`lantai/core/text.py::normalize_for_index(text) -> str`：**NFKC（折宽度）→ 剥零宽/bidi 控制符 → NFC（收敛分解形态）**，幂等、falsy 入参返回 `""`。剥除集（Cf 类零宽 LRM/RLM/BOM + bidi LRE/RLE/PDF/LRO/RLO/LRI/RLI/FSI/PDI）必须显式列出——NFKC 对这些字符无操作，靠兼容分解剥不掉。

消费点全部过缝、口径同源：FTS 索引副本（`sync_fts`/`index_fts`）、查询关键字抽取（`_bm25_keywords`，AND/OR 两路共用）、案牍去重键与列表匹配（`_normalize_duplicate_text` + `list_work_items`）、反思聚类签名（`_get_signature`）、检索指纹（`_norm_hash`）。

### 2.2 边界：归一索引副本 ≠ 内容改写（本 ADR 的核心取舍）

`MemoryItem.content` 是用户资产，与獬豸「不改写用户字」（`injection_guard` 只检测不改写）同一条边界：**归一缝只许作用于派生数据**——索引副本、哈希键、匹配串。理由：

- 原文里 ZWSP/全角可能是用户的刻意排版（诗词分行、日文全角习惯）；
- 改写正文不可逆（有损变换），漏归一只造成可修复的召回 miss（FTS 索引可重建）——「宁 miss 不脏写」在这条边界上的具体化；
- 獬豸检测闸的瞬态归一（`injection_guard.py` NFKC 后还原）是同一原则的既有先例。

哨兵测试（`test_index_copy_normalized_content_untouched`）钉住：归一后 `MemoryItem.content` 与原文逐字节相同、ZWSP 仍在。

### 2.3 存量：FTS 索引全量重建，不做数据迁移

索引是派生数据——`rebuild_fts(session)` truncate 后从 `memoryitem` 逐行重灌（重灌路径过缝），运维入口 `scripts/rebuild_fts.py`（幂等）。不涉及正文改写、不需要 schema 迁移；案牍去重键/签名/指纹的存量行不追溯（键是行为快照，下次写入自然归一——历史分组差异宁 miss 不脏写）。

### 2.4 不归一的面（如实登记）

- **向量通道**：embedding 输入不过缝——embedding 模型对全角/零宽的语义敏感度远低于 trigram 的码位敏感性，且 Chroma 侧数据同样是派生数据但重建代价高；真出问题另立案。
- **`coalesce` 幂等指纹 / 石室完整性摘要**：字节级哈希是**正确**行为（完整性用途必须字节级），勿顺手"修"（票面已核查干净，列此防未来误改）。
- **正文写侧**（`add`/`raw_add`/dialogue/obsidian/RSS）：零触碰——见 §2.2。

## 3. 命名

不引入新中文正名：`normalize_for_index` 为内部工具函数英文名，非面向用户的新概念。ADR-0013 的登记义务只对**新的正式中文名**生效，本决策无新增。

## 4. 后果

- 全角↔半角、NFC↔NFD、ZWSP 撕裂三类确定性零召回从索引与查询两面消除（`tests/test_unicode_normalization_smoke.py` 钉住）；
- 案牍全角标题变体归并同组，待审队列不再出双份工作项；
- 反思聚类签名/检索指纹对全角变体收敛同桶同指纹；
- 存量库需跑一次 `scripts/rebuild_fts.py` 才能享受归一召回（新写入即时生效）；
- 正文的 ZWSP/全角原样保留——检索副本归一，用户字不动。

## 5. 关联

- [ADR-0084](0084-vetoed-receipt-terminal-state.md)（同窗口的检索面收口——其 §2.5/2.6 的抹文是内容脱敏，本 ADR 是召回归一，互不重叠）
- 獬豸检测闸「只检测不改写」（`lantai/security/injection_guard.py`）
- 票据：`.scratch/v0.68-next-window-2026-11/issues/45-unicode-normalization-seam-absent.md`
