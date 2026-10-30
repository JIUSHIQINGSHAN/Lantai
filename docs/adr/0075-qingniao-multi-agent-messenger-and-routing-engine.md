# ADR-0075: 青鸟（Qingniao）——多智能体记忆信使与异步通信路由引擎

**日期**: 2026-10-30  
**状态**: Accepted  
**决策者**: 大哥、Claude  
**来源**: 多智能体记忆协同演进与 ADR-0013 意象候选转正（`三大来源器物自然 -> 青鸟`）

---

## 1. 背景

在智能体集群协作、多智能体交互与自治联邦生态中，智能体间需要一种高可靠、防篡改、结构化可追溯的**认知记忆跨体传递与信使路由机制**：
1. **多智能体缺乏专职的记忆信使通信与异步分发引擎**：
   既有系统记忆往往局限于单个智能体本地或依赖扁平共享表，缺乏类似青鸟传书般具有寻址（`target_agent_id`）、信函封装（`QingniaoLetter`）、信封防注入围栏、送达回执（ACK/NACK）与投递生命周期追踪的一级信使引擎；
2. **缺乏记忆信函的防注入数据围栏与行级信道隔离**：
   跨智能体接收外部信使消息时，容易受到恶意构造的认知内容或提示注入（Prompt Injection）攻击；信函正文必须强制经由樊篱（`<memory_data id="qingniao-...">`）严密包裹，并校验发送方与接收方的租户/智能体权限（宁 miss 不脏写）；
3. **缺乏专职的认知上下文青鸟信使切面（CognitiveContext Qingniao Aspect）**：
   下游 LLM 在执行协作决策时，需要一目了然获取「待收信函与跨智能体关键公文通报（青鸟切面）」，认知上下文必须予以结构化呈现；
4. **命名与规范治理**：
   落实 ADR-0013 意象池三大来源之器物/自然意象，正名「**青鸟**」（Qingniao · 李商隐《无题》「蓬山此去无多路，青鸟殷勤为探看」；《山海经》西王母三青鸟，代指传递神圣书简与关键信讯之专职信使）。

---

## 2. 决策

### 2.1 命名正名与意象出处
正式确立中文意象名：**青鸟（Qingniao）**。
- **出处**：典籍自然意象与信使典故。李商隐《无题》：「蓬山此去无多路，青鸟殷勤为探看。」《山海经·海内北经》：「有三青鸟，主为西王母取食者，亦通传音讯。」
- **含义**：在认知架构中，充当多智能体集群间的神圣专职信使，负责高保真、防注入、有回执的记忆信函路由与异步送达。

### 2.2 领域模型与核心数据结构
定义青鸟信函契约 `QingniaoLetter`：
```python
@dataclass
class QingniaoLetter:
    letter_id: str
    sender_agent_id: str
    receiver_agent_id: str
    subject: str
    content: str
    priority: str  # high | normal | low
    status: str  # pending | delivered | acknowledged | rejected
    fenced_content: str
    user_id: str | None = None
    tenant_id: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    acknowledged_at: datetime | None = None
```

定义派发回执契约 `QingniaoDispatchResult`：
```python
@dataclass
class QingniaoDispatchResult:
    is_dispatched: bool
    letter_id: str
    delivered_count: int
    dispatched_at: datetime = field(default_factory=utcnow)
```

### 2.3 核心引擎 `QingniaoEngine`
在 `lantai/cognition/qingniao.py` 交付：
- `dispatch_letter(...)`: 封装信函，经樊篱（`wrap_as_data`）隔离并写入信箱；
- `fetch_inbox(...)`: 拉取待收信件，结构化输出并保障租户隔离；
- `acknowledge_letter(...)`: 确认（ACK）或拒收（REJECT）信函，完成生命周期闭环。

---

## 3. 影响

- 建立起标准化的多智能体异步信使通信标准与安全围栏防护；
- 认知上下文具备 `qingniao_digest` 专职切面；
- 维持不 mock 冒烟测试覆盖度与行级 ACL 铁律。
