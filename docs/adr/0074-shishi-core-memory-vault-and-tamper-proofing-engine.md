# ADR-0074: 石室（Shishi）——高密核心记忆金匮封存与防篡改硬核锚定引擎

**日期**: 2026-10-30  
**状态**: Accepted  
**决策者**: 大哥、Claude  
**来源**: 核心记忆安全防护体系演进与 ADR-0013 意象候选转正（`三大来源官署机构 -> 石室`）

---

## 1. 背景

在智能体复杂自治演化、长程多轮交互与多智能体协作场景中，存在最高特权且直接影响 Agent 决策行为底线的记忆资产——**核心记忆硬核（Core Memory Blocks: identity 身份原则 / policy 行为铁律 / task 任务基石）**：
1. **防篡改与完整性验真缺失**：
   既有核心记忆缺乏类似金匮石室般高密防篡改、不可变历史版本审计与一致性哈希指纹签名保障，一旦数据库行被意外或恶意修改，缺乏主动感知手段；
2. **缺乏核心记忆不可变（immutable）锁定**：
   缺乏确定性的「金匮石室」防篡改阻断机制，任何写入请求在缺乏严格锁定时可能误覆盖已定型行为铁律；
3. **安全注入防御（樊篱贯穿）**：
   核心记忆直接注入 Prompt 决策面，若未经过樊篱（`<memory_data>`）防护并显式标记最高优先级，容易被恶意提示注入所干扰；
4. **命名与规范治理**：
   落实 ADR-0013 意象池三大来源之官署/机构意象，正名「**石室**」（Shishi · 《史记·太史公自序》「迁为太史令，紬石室金匮之书」；汉代藏秘书之所，石匮封存，金泥玉检，防焚防篡，坚如磐石）。

---

## 2. 决策

### 2.1 命名正名与意象出处
正式确立中文意象名：**石室（Shishi）**。
- **出处**：古代藏秘之所与典籍封存官署意象。汉司马迁《史记·太史公自序》：「迁为太史令，紬石室金匮之书。」
- **含义**：在认知架构中，将智能体最核心、最神圣不可动摇之身份、原则与铁律，作为「金匮石室」秘籍硬核封存，计算哈希指纹，施加不可变锁定，提供全生命周期防篡改、完整性验真与樊篱隔离锚定。

### 2.2 领域模型与核心数据结构
定义金匮核心记忆封存块契约 `ShishiVaultBlock`：
```python
@dataclass
class ShishiVaultBlock:
    block_key: str  # identity | policy | task
    content: str
    content_digest: str  # SHA-256 哈希签名
    version: int
    is_immutable: bool
    namespace: str
    fenced_content: str
    user_id: str | None = None
    tenant_id: str | None = None
    updated_at: datetime = field(default_factory=utcnow)
```

定义完整性验真结果契约 `IntegrityVerificationResult`：
```python
@dataclass
class IntegrityVerificationResult:
    is_valid: bool
    total_blocks: int
    verified_blocks: int
    tampered_blocks: list[str]
    tamper_details: list[dict[str, Any]]
    verified_at: datetime = field(default_factory=utcnow)
```

### 2.3 核心引擎 `ShishiEngine`
在 `lantai/cognition/shishi.py` 实现专职引擎：
1. `seal_core_memory(block: str, content: str, is_immutable: bool = True, namespace: str = "default")`：
   - 封存核心记忆，计算内容 SHA-256 签名并自增版本；
   - 检查既有块的 `is_immutable` 状态，若已锁定且非管理员修改直接拒绝（宁 miss 不脏写）；
2. `verify_integrity(namespace: str = "default")`：
   - 全量扫描比对数据库中核心记忆内容的当前哈希与元数据签名指纹，发现任何未授权篡改即报警；
3. `get_sealed_blocks(namespace: str = "default")`：
   - 安全读取已封存的核心记忆，强制樊篱包裹渲染。

### 2.4 多租户行级 ACL 与宁 miss 不脏写
- 强制校验 `tenant_id` 与 `user_id`，非特权用户仅可访问与封存属于自己的核心记忆块；
- 严禁跨租户未授权改写或读取。

### 2.5 认知上下文切面与 MCP 工具扩容
1. **认知上下文**：`CognitiveContext` 新增 `shishi_digest` 切面，在 `to_prompt()` 中渲染 `## Core Memory Vault (石室 · {count})`（樊篱防护）；
2. **MCP 工具池**：注册 `shishi_vault` 工具，工具池由 86 项扩充至 **87 项**，保持 1:1 双向映射守卫。

---

## 3. 影响

- 核心记忆的安全防护与可审计性从单纯的数据行提升为具备加密签名指纹、防篡改锁定与验真回环的认知硬核；
- 认知上下文切面进一步完善，为 downstream LLM 提供坚实不移的核心身份与行为铁律；
- MCP 协议工具池扩展至 87 项，全面对齐。
