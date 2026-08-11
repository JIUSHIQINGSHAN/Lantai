"""中文记忆评测集 v1（研究方向「一年内」档）：面向中文场景的遗忘质量自测数据。

覆盖维度：
- typo        中文错别字容错——FTS5 trigram 对词边界插入/删除容错（实测确认：
              中间错字需向量层兜底，故评测集用边界型错字保证 FTS 兜底路径确定性）
- fresh       对照组（管道自检，应全命中）
- stale       遗忘（apply_forgetting 归档后不应残留）
- temporal    Chronos 双时间轴（未生效过滤 / 过期降权后新值在前）
- superseded  矛盾取代（新值应命中；旧值残留率诚实测量）

查询设计约束：query 必须与目标内容共享 ≥1 个 trigram 子串，
保证向量不可用时（FTS 兜底路径）测试仍确定。
"""
from lantai.eval.forgetting_quality import EVAL_NAMESPACE


def build_chinese_dataset() -> dict:
    """返回评测数据集（纯数据，无副作用）。"""
    return {
        "name": "chinese-memory-v1",
        "namespace": EVAL_NAMESPACE,
        "description": "中文记忆评测集 v1：错别字容错 / 时效 / 遗忘 / 矛盾取代 / 对照",
        "apply_forgetting": True,
        "cases": [
            # ── 错别字容错（FTS trigram）────────────────────────────
            {"category": "typo", "query": "器学习用于图像识别",
             "seeds": [{"content": "机器学习用于图像识别与自然语言处理",
                        "lane": "fact"}],
             "target": 0},
            {"category": "typo", "query": "量检索支持错别字",
             "seeds": [{"content": "向量检索支持错别字容错与子串匹配",
                        "lane": "fact"}],
             "target": 0},
            {"category": "typo", "query": "私有化部署方",
             "seeds": [{"content": "支持离线运行的私有化部署方案", "lane": "fact"}],
             "target": 0},
            {"category": "typo", "query": "提示词模板支持变量替换",
             "seeds": [{"content": "自定义提示词模板支持变量替换", "lane": "rule"}],
             "target": 0},

            # ── 对照组（管道自检）──────────────────────────────────
            {"category": "fresh", "query": "喝无糖咖啡",
             "seeds": [{"content": "用户偏好：喜欢喝无糖咖啡", "lane": "preference"}],
             "target": 0},
            {"category": "fresh", "query": "周会时间",
             "seeds": [{"content": "团队周会时间是每周一上午十点", "lane": "fact"}],
             "target": 0},
            {"category": "fresh", "query": "服务端口",
             "seeds": [{"content": "服务端口配置为 8767", "lane": "fact"}],
             "target": 0},

            # ── 遗忘（已归档不应残留）──────────────────────────────
            {"category": "stale", "query": "看了部电影叫",
             "seeds": [{"content": "昨天看了部电影叫流浪地球", "lane": "chat",
                        "created_days_ago": 90, "importance": 0.1}],
             "forbidden": [0]},
            {"category": "stale", "query": "项目文档用英文写",
             "seeds": [{"content": "偏好：项目文档用英文写", "lane": "preference",
                        "created_days_ago": 200, "importance": 0.1}],
             "forbidden": [0]},

            # ── 时效（Chronos 双时间轴）────────────────────────────
            {"category": "temporal", "query": "项目上线时间",
             "seeds": [
                 {"content": "新项目上线时间预计在下季度", "lane": "fact"},
                 {"content": "新项目 3.0 版本下月上线", "lane": "fact",
                  "valid_from_days": 30},
             ], "preferred": 0, "peer": 1},
            {"category": "temporal", "query": "项目截止日期",
             "seeds": [
                 {"content": "项目截止日期是 4月1号", "lane": "fact"},
                 {"content": "项目截止日期是 3月15号", "lane": "fact",
                  "valid_to_days": 10},
             ], "preferred": 0, "peer": 1},

            # ── 矛盾取代（supersedes 边）───────────────────────────
            # 旧值早创建（衰减更低但未达归档阈值）→ decay 分确定性让新值在前；
            # 旧值仍可召回，残留率是诚实测量而非构造失效。
            {"category": "superseded", "query": "公司域名",
             "seeds": [
                 {"content": "公司域名是 example.com", "lane": "fact",
                  "created_days_ago": 60},
                 {"content": "公司域名改为 new-example.com", "lane": "fact"},
             ],
             "edges": [{"source": 1, "target": 0}],
             "target": 1, "preferred": 1, "peer": 0},
            {"category": "superseded", "query": "API 密钥",
             "seeds": [
                 {"content": "API 密钥存储在 config.py", "lane": "fact",
                  "created_days_ago": 60},
                 {"content": "API 密钥改为环境变量注入", "lane": "rule"},
             ],
             "edges": [{"source": 1, "target": 0}],
             "target": 1, "preferred": 1, "peer": 0},
        ],
    }
