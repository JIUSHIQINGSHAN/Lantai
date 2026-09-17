# 兰台记忆（Lantai）— 上下文

## 项目定位

AI Agent 长期记忆管理系统——摄取、闸门、演化、检索、遗忘的完整链路。

## 词汇表

> **阅读规则**：本表供人类读者与 Agent 理解领域语言。每个条目均含“通俗解释”（它解决什么问题、读者何时遇到它）与“来源”（“命名”说明文化或内部命名依据，“设计”链接实现决策或代码/规范入口）。纯底层实现字段仅在具有可观察价值时以技术字段形式保留。

| 术语 | 通俗解释 | 来源 |
|------|----------|------|
| **兰台（Lantai）** | 项目主系统名；为 AI Agent 保存、检索、演化与安全遗忘长期记忆的档案库。 | 命名：汉代皇家档案馆「兰台」；设计：见 [ADR-0013](docs/adr/0013-naming-system.md) |
| **锦囊（Jinnang）** | 待审候选队列；当系统提取出的事实置信度不足或存在潜在冲突时，暂存此处等待人工或特权 Agent 裁决。 | 命名：成语「锦囊妙计」；设计：见 [ADR-0013](docs/adr/0013-naming-system.md)、[ADR-0026](docs/adr/0026-candidate-admission-triage.md)（代码 `pending_review` 队列） |
| **案牍（Andu）** | 控制台统一待办工作台；把待审候选、演化提案、冲突事件、参数建议等散落事项汇总为一张可排序、可处置的只读清单。 | 命名：刘禹锡《陋室铭》「无案牍之劳形」；设计：见 [ADR-0013](docs/adr/0013-naming-system.md)（控制台 `work_item_service`） |
| **lane**（分轨） | 记忆的类型通道（如事实、规则、偏好、经验、闲聊）；不同通道拥有独立的遗忘速度与检索权重。 | 命名：项目内部技术命名（分轨）；设计：见 [ADR-0003](docs/adr/0003-coalesce-buffer-key.md) |
| **tier**（层级） | 记忆的生命周期深度，区分为临时的“工作记忆”与长期的“长期记忆”，超时未被强化的临时记忆会自动归档。 | 命名：项目内部技术命名（层级）；设计：见 `lantai/models/tables.py`（`MemoryItem.tier`） |
| **gate**（闸门） | 新记忆进入系统的第一道质检门；综合新颖度、置信度与矛盾检测做出放行、拒绝或转入待审的裁决。 | 命名：意象取材「水闸控流」；设计：见 [ADR-0013](docs/adr/0013-naming-system.md)、`lantai/gate/decision.py` |
| **coalesce**（潮波合并） | 连续短消息的异步缓冲机制；将相近对话合并后再调用大模型提炼，避免高频细碎调用消耗资源。 | 命名：意象取材「潮水成波、批量冲刷」；设计：见 [ADR-0003](docs/adr/0003-coalesce-buffer-key.md) |
| **fastpath**（白名单直写） | 常见确定性句式（如用户自我声明、明确指令）绕过大模型直接安全存入记忆库的高速通道。 | 命名：项目内部技术名（ADR-0013 候选意象「直书」）；设计：见 `parsing/fastpath.py` 与 [ADR-0013](docs/adr/0013-naming-system.md) |
| **candidate**（候选记忆） | 从原始输入中提取出的结构化知识半成品，必须通过闸门检验后才能正式转化为系统记忆。 | 命名：项目内部技术命名（候选）；设计：见 `lantai/models/tables.py`（`MemoryCandidate`）与 [ADR-0026](docs/adr/0026-candidate-admission-triage.md) |
| **proposal**（提案） | 候选记忆过闸后生成的变更申请（如新增、合并、更新或废弃旧记忆），经确认后才会落库生效。 | 命名：项目内部技术名（ADR-0013 候选意象「拟议」）；设计：见 `lantai/models/tables.py`（`MemoryProposal`） |
| **checkpoint**（检查点） | 记忆单项变更前后的对比快照，专门用于误操作或异常变更时的精确版本回滚。 | 命名：项目内部技术命名（快照）；设计：见 `lantai/models/tables.py`（`MemoryCheckpoint`） |
| **archived**（归档记忆） | 因久未使用或严重衰减而退出常规检索的历史记忆，保留原始数据但不再干扰日常对话。 | 命名：项目内部技术名（ADR-0013 候选意象「尘封」）；设计：见 [ADR-0005](docs/adr/0005-forgetting-semantics.md) |
| **Shell Hook** | 终端环境下的零依赖记忆注入通道；命令行工具以此在毫秒级内获取当前会话相关的记忆上下文。 | 命名：项目内部技术命名；设计：见 [ADR-0006](docs/adr/0006-shell-hook-contract.md) |
| **verbatim**（原文直存） | 对长代码、系统日志或精密配置等不宜提炼的内容进行原文完整存储的模式，以哈希去重并保留原始字句。 | 命名：项目内部技术命名（原文直存）；设计：见 [ADR-0009](docs/adr/0009-raw-drawer-verbatim.md) |
| **conflict_event**（冲突账本） | 记录新旧记忆在规则或事实层发生矛盾的审计账簿，记录冲突双方与裁决历史，供人工或探针排解。 | 命名：项目内部技术名（ADR-0013 候选意象「参商」）；设计：见 [ADR-0010](docs/adr/0010-conflict-resolution-layer.md) |
| **Skill 资产**（可注入技能） | 包含明确步骤与执行逻辑的程序性记忆；检索命中时以规范步骤块呈现，供 Agent 照章执行。 | 命名：项目内部命名（ADR-0013 候选意象「法门」）；设计：见 [ADR-0011](docs/adr/0011-skill-asset.md) |
| **scene**（场景聚合） | 将同一任务或主题下的多条相关记忆自动聚类形成的场景单元；检索时先展示场景概要，按需下钻详情。 | 命名：项目内部技术名（ADR-0013 候选意象「卷宗」）；设计：见 [ADR-0012](docs/adr/0012-scene-layer.md) |
| **provenance**（提取来源） | 记录记忆是由哪套提示词、哪个模型及何时提取的血统证明，用于追溯记忆质量变化的根因。 | 命名：项目内部技术命名（溯源）；设计：见 [ADR-0015](docs/adr/0015-provenance.md) |
| **ACL**（访问收窄） | 多智能体权限隔离机制；根据 Agent 标识限定其可检索和写入的记忆分轨，防止跨角色信息越权。 | 命名：项目内部技术命名（权限控制）；设计：见 [ADR-0013](docs/adr/0013-naming-system.md) 命名登记 |
| **冷启动导入** | 系统初次搭建时批量载入历史资料的双通道机制；支持无损原文直存与对话链模拟摄取。 | 命名：项目内部业务命名；设计：见 [ADR-0018](docs/adr/0018-import.md) 及 `scripts/import_jsonl.py` |
| **vault**（档案） | 记忆库的只读浏览视图；供维护者分页查看全部存量记忆状态、衰减情况与待审列表。 | 命名：项目内部命名（档案库）；设计：见 [ADR-0013](docs/adr/0013-naming-system.md) 命名登记与 `/ui/vault` 路由 |
| **offload**（上下文卸载） | 超长记忆落盘为离线文件、会话中仅注入摘要和路径的技术；避免单条超长记忆挤占模型上下文。 | 命名：项目内部技术命名（上下文卸载）；设计：见 [ADR-0016](docs/adr/0016-offload.md) |
| **记忆 Wiki** | 将零散场景与技能聚合生成的持续维护知识库；支持目录导览、主题综述与双向链接下钻。 | 命名：项目内部命名；设计：见 [ADR-0017](docs/adr/0017-wiki.md) |
| **tree**（记忆分类树） | 按业务或项目主题层级构建的父子节点树；支持记忆按树状目录归类与整树统计。 | 命名：项目内部技术命名（分类树）；设计：见开发票据 `v0.7 票据 01` 与 `MemoryNode` 实体 |
| **crystal**（技能结晶） | 将高频共现的重复记忆聚类转化为可复用技能的推荐机制；经人工或审批通过后固化为系统技能。 | 命名：项目内部命名（技能结晶）；设计：见开发票据 `v0.7 票据 02` 与 `SkillCrystal` 实体 |
| **mem: 命令** | 供维护者或 Agent 在对话中主动发起记忆维护的指令集（如同步索引、手动沉淀技能）。 | 命名：借鉴 TencentDB Agent Memory 命令体系；设计：见 [ADR-0014](docs/adr/0014-mem-command.md) |
| **graph**（记忆星图） | 以网状图谱可视化展示记忆与原始文档之间的支撑、细化或矛盾关系，理清知识脉络。 | 命名：意象取材「观星定位之星图」；设计：见 [ADR-0013](docs/adr/0013-naming-system.md) 及 `GET /graph` 路由 |
| **目识**（Vision 多模态） | 图片感知与截屏摄取通道；解析图像并生成结构化描述，使多模态视觉信息能作为记忆持久留存。 | 命名：《说文解字》「目」与「识」，以目认知；设计：见 [ADR-0013](docs/adr/0013-naming-system.md) 及 `scripts/screenshot_memory.ps1` |
| **烽燧**（recall_chain 广播链） | 以一条种子记忆为引子，通过多跳关联检索逐层扩散召回隐式上下文的广播检索机制。 | 命名：贾谊《治安策》「斥候望烽燧」，接力传警；设计：见 [ADR-0013](docs/adr/0013-naming-system.md) 及 `GET /recall/chain` 路由 |
| **digest**（每日盘点） | 系统每日清晨自动生成的知识简报，盘点过去一天的记忆增减、待审积压与检索活跃度。 | 命名：项目内部技术名（ADR-0013 候选意象「起居注」）；设计：见 [docs/daily-digest.md](docs/daily-digest.md) |
| **命名体系** | 规范系统各模块、机制及版本代号的命名准则；要求取材传统文化意象，做到名实相副、有据可查。 | 命名：项目治理规范；设计：见 [ADR-0013](docs/adr/0013-naming-system.md) |
| **反思**（reflect/蒸馏） | 每日后台自动执行的记忆自我审视回环；根据健康指标提炼新认知、发现潜在矛盾并提交优化提案。 | 命名：《论语》「吾日三省吾身」；设计：见 [docs/plans/reflection-module-spec.md](docs/plans/reflection-module-spec.md) |
| **观察期**（察窗） | 反思阈值调整前的真实运行数据收集窗口；需在滑动窗口内积累足够合格运行样本后才触发校准。 | 命名：项目内部命名，规则名「察窗」；设计：见 [ADR-0027](docs/adr/0027-observation-window-counting.md) |
| **沙汰**（候选入队信噪分离） | 在候选进入人工待审队列前筛除纯闲聊等无价值噪音的地板过滤机制，避免无效候选项堆积。 | 命名：《世说新语》「沙汰」，淘洗淘汰之意；设计：见 [ADR-0026](docs/adr/0026-candidate-admission-triage.md) |
| **校雠**（三态去重） | 综合余弦相似度预筛与语义结构判别的智能去重机制；精准区分“近义合并”、“同主体更新”与“新事实插入”。 | 命名：刘向《别录》校雠订误；设计：见 [ADR-0013](docs/adr/0013-naming-system.md)、[ADR-0019](docs/adr/0019-dedup-structural-relation.md) |
| **底本**（Diben，会话快照） | 在会话结束或上下文压缩时记录的现场快照，涵盖在做、下一步、决策等五大要素，供下次会话无缝继承。 | 命名：版本学意象，校勘所据之定本；设计：见 [ADR-0013](docs/adr/0013-naming-system.md)、[ADR-0021](docs/adr/0021-session-checkpoint.md) |
| **拾遗**（Shiyi，检索韧性降级） | 当外部向量检索服务超时或异常时，平滑降级为本地关键词与全文检索的多级容灾保障，杜绝检索击穿。 | 命名：唐代谏官官职「拾遗」，取拾遗补阙之义；设计：见 [ADR-0013](docs/adr/0013-naming-system.md)、[ADR-0028](docs/adr/0028-zero-recall-remediation.md) |
| **器识**（Qishi，人格基座） | Agent 保持稳定角色与原则的底座；确立长期不变的语言风格、行为戒律与认知底色，且不随时间遗忘衰减。 | 命名：《新唐书·裴行俭传》「士之致远，先器识而后文艺」；设计：见 [ADR-0013](docs/adr/0013-naming-system.md)、[ADR-0029](docs/adr/0029-persona-base.md) |
| **披沙**（Pisha，候选精炼） | 对处于置信度模糊带的候选记忆进行二次结构化提纯，消除代词指代歧义与口语废话，提升记忆质量。 | 命名：《世说新语·德行》「披沙拣金，往往见宝」；设计：见 [ADR-0013](docs/adr/0013-naming-system.md)、[ADR-0030](docs/adr/0030-candidate-refine.md) |
| **考功**（Kaogong，价值演化） | 根据记忆在实际交互中的被采纳率与用户反馈，对全库记忆进行功过评定；晋升高频有益记忆，淘汰劣质记忆。 | 命名：唐代吏部「考功司」，掌官吏功过品级考评；设计：见 [ADR-0013](docs/adr/0013-naming-system.md)、[ADR-0031](docs/adr/0031-kaogong-weight-evolution.md) |
| **札记**（Zhaji，工作区暂存便签） | 供 Agent 在复杂多轮交互中主动读写的临时工作便签，辅助记录多步骤推理中的阶段性要点。 | 命名：古代读书摘记要点之木简便签；设计：见 [ADR-0013](docs/adr/0013-naming-system.md)、[ADR-0032](docs/adr/0032-session-scratchpad.md) |
| **潜移**（Qianyi，异步摄取管道） | 将对话提炼与入库流水线转入后台非阻塞线程池的异步架构；使对话端毫秒级返回，彻底杜绝大模型调用阻塞。 | 命名：《文心雕龙》「潜移暗引，莫之能知」；设计：见 [ADR-0013](docs/adr/0013-naming-system.md)、[ADR-0033](docs/adr/0033-async-ingest-pipeline.md) |
| **辨域**（Bianyu，三维硬隔离） | 将记忆按“用户偏好”、“会话临时上下文”与“智能体准则”三个维度严格切分的隔离机制，杜绝跨场景交叉污染。 | 命名：《周礼·春官·宗伯》「以辨天地四时之域」；设计：见 [ADR-0013](docs/adr/0013-naming-system.md)、[ADR-0034](docs/adr/0034-domain-isolation.md) |
| **贯珠**（Guanzhu，图谱二度联想） | 沿着实体关系图谱展开一至两步跳跃扩散的联想检索；能跨越字面局限，顺藤摸瓜检索出隐藏的关联知识。 | 命名：《汉书·景十三王传》「如贯珠焉」；设计：见 [ADR-0013](docs/adr/0013-naming-system.md)、[ADR-0035](docs/adr/0035-graph-association-retriever.md) |
| **沉潜**（Chenqian，闲时折叠压缩） | 模仿人脑睡眠巩固机制；在闲时对同场景的高密度碎片记忆进行归纳折叠生成总括记忆，并修剪深度衰减噪音。 | 命名：《荀子》「沉潜以思」；设计：见 [ADR-0013](docs/adr/0013-naming-system.md)、[ADR-0036](docs/adr/0036-sleep-memory-consolidation.md) |
| **探颐**（Tanyi，主动探针） | 当检测到记忆存在模糊或冲突时，在对话回复中主动发起自然提问向用户求证，并在次轮自动闭环解决冲突。 | 命名：《易·系辞上》「探赜索隐，钩深致远」；设计：见 [ADR-0013](docs/adr/0013-naming-system.md)、[ADR-0037](docs/adr/0037-proactive-memory-probing.md) |
| **悬镜**（Xuanjing，可视化控制台） | 兰台系统的全功能单页 Web 管理中台（Lantai Studio），提供待办审批、人格编辑、检索演练与星图总览。 | 命名：宝镜高悬、洞烛幽微之意；设计：见 [ADR-0013](docs/adr/0013-naming-system.md)、[ADR-0038](docs/adr/0038-visual-management-studio.md) |
| **持节**（Chijie，智能体自主审批） | 赋予受信任的外部 AI 智能体自主治理记忆库的协议规范；支持智能体自动巡检、批量审批候选与清理噪音。 | 命名：汉唐典制天子特使「持节」巡行决断；设计：见 [ADR-0013](docs/adr/0013-naming-system.md)、[ADR-0039](docs/adr/0039-agent-autonomous-triage.md) |
| **认知闭环**（Cognitive Loop） | 从任务失败记录中归纳经验、沉淀出新信念与规则，并在后续类似任务中自动生效的自主学习机制。 | 命名：认知心理学与 Agent 学习机制命名；设计：见 `docs/benchmarks/behavioral-learning-benchmark.md` |
| **司天**（Sitian，运行监控面板） | 系统后台健康度与性能的统一监测大盘；集中掌控进程状态、存储容量、记忆吞吐、任务调度及告警事件。 | 命名：古代观测天象、预报灾异的官署「司天监」；设计：见 [ADR-0013](docs/adr/0013-naming-system.md)、[ADR-0045](docs/adr/0045-sitian-ops-monitor-panel.md) |
| **知命**（Zhiming，知识生命状态机） | 知识生命周期演进模型；追踪知识从活跃、衰减弱化、被新知识取代到最终退役的全流程。 | 命名：《论语》「不知命，无以为君子也」；设计：见 [ADR-0013](docs/adr/0013-naming-system.md) 及 [v0.4 规范](docs/plans/v0.4-lifecycle-middleware.md) |
| **直断**（Zhiduan，裁决理由追踪） | 冲突消解引擎在仲裁新旧矛盾时生成的结构化裁决依据；清晰记录胜出方的核心评分优势与判定原因。 | 命名：直截明断、断狱有据之意；设计：见 [ADR-0013](docs/adr/0013-naming-system.md) 及 `lantai/cognition/conflicts.py` |
| **润物**（Runwu，自适应认知中间件） | 无需 Agent 手动调工具查询、在处理链路中透明为当前请求自动装载相关规则与历史教训的注入技术。 | 命名：杜甫《春夜喜雨》「随风潜入夜，润物细无声」；设计：见 [ADR-0013](docs/adr/0013-naming-system.md) 及 `lantai/runtime/middleware.py` |
| **吉金**（Jijin，UI 主题） | 悬镜控制台默认深色皮肤；以青铜器拓片质感为底色，点缀铜绿与鎏金纹理，体现沉稳厚重的历史感。 | 命名：古籍中对青铜彝器的尊称（《墨子》等）；设计：见 [ADR-0013](docs/adr/0013-naming-system.md)、[ADR-0038](docs/adr/0038-visual-management-studio.md) |
| **漏窗**（Louchuang，UI 主题） | 悬镜控制台浅色皮肤；汲取苏州园林漏窗移步换景的意境，采用绢黄底色与月洞门造型卡片。 | 命名：苏州园林建筑构件「漏窗」；设计：见 [ADR-0013](docs/adr/0013-naming-system.md)、[ADR-0038](docs/adr/0038-visual-management-studio.md) |
| **缥缃**（Piaoxiang，版本代号） | v0.14.0 发行版本代号；象征古籍书卷，契合兰台为 AI 守护长期记忆档案的系统定位。 | 命名：古人对淡青与淡黄色丝帛书衣的称谓，借指书卷；设计：见 [ADR-0013](docs/adr/0013-naming-system.md) 版本代号登记 |
| **绳墨**（Shengmo，版本代号） | v0.15.2 发行版本代号；寓意以严谨准绳定曲直，对应反思校准、测试门禁与发布纪律收口。 | 命名：《礼记·经解》「绳墨之于曲直」；设计：见 [ADR-0013](docs/adr/0013-naming-system.md) 版本代号登记 |
| *decay_score*（技术字段） | 记忆保持强度分值（0.0~1.0）；随时间指数衰减，跌破极低阈值后触发休眠归档。 | 命名：项目内部技术字段；设计：见 `lantai/models/tables.py` 及 [ADR-0005](docs/adr/0005-forgetting-semantics.md) |
| *search_trace*（技术字段） | 检索诊断调试数组；开启后记录检索执行各步骤的耗时、候选条数与得分分布。 | 命名：项目内部技术字段；设计：见 `lantai/retrieval/hybrid.py` 诊断输出 |
| *water_level*（技术字段） | 潮波合并缓冲区的水位指标；实时反映未冲刷的消息堆积量与活跃会话数，用于写入节流监控。 | 命名：项目内部技术字段；设计：见 [ADR-0003](docs/adr/0003-coalesce-buffer-key.md) 及 `/stats` 路由 |
| *promotion_trace*（技术字段） | 知识晋升评分快照（JSON 格式）；记录单条记忆被升格为高阶信念或规则时的多维度打分依据。 | 命名：项目内部技术字段；设计：见 `lantai/models/tables.py` 及 [ADR-0044](docs/adr/0044-cognition-evolution-boundary.md) |
| *failure_pattern*（技术字段） | 从失败记录中抽象出的认知模式标识；用于预警同类操作风险，防止 Agent 重蹈覆辙。 | 命名：项目内部技术字段；设计：见 `lantai/cognition/reflection.py` 及 [ADR-0044](docs/adr/0044-cognition-evolution-boundary.md) |
| *facade rule*（工程规范） | 系统重构纪律：只搬迁模块位置、不更改业务语义与接口调用路径，保证旧 import 全绿。 | 命名：经典设计模式（外观模式 Facade）；设计：见 [ADR-0001](docs/adr/0001-facade-rule.md) |
| *service layer*（工程规范） | 架构分层约定：接口层仅负责网络请求协议解析，所有核心业务逻辑统一沉淀在 service 服务层。 | 命名：经典企业应用架构模式（服务层）；设计：见 [ADR-0001](docs/adr/0001-facade-rule.md) |
