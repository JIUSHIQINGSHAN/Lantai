r"""票 50：日志卫生面——用户可控值无单行化/截断直落日志（四臂红测）。

八处病灶（v0.68 立案轮 7 日志卫生面子审计，同修法族）：
1. [P2] 磨勘 query 原话整条进日志（REST %0A 换行可伪造日志行 + 无截断）；
2. [P2] 沉潜成功日志携带记忆正文 content[:30]（正文第二份无生命周期留存）；
3. [P3] validate_api_url 异常文本携带完整 URL（key-in-URL 密钥入日志）；
4. [P3] 探颐 20 字窗 / RSS URL / 札记 session_id / SDK 异常透传 / 复合串
   错误路径（idempotency key、coalesce key、request_id）——由臂 4 哨兵
   统一锁定 + 臂 1/3 行为面代表。

哨兵（臂 4）：AST 全仓扫描——logger 插值参数表达式内出现用户可控原文变量
（query/content/url/key 等）即违例，除非该参数整体经 loggable() 包装；
长度型合法位点（len(content) 等）白名单登记并注释理由。

禁 mock 被测链路：臂 1 真路由→真服务→真内存库（零替身）；臂 2 真折叠链
（仅替 LLM 提纯段——外部 LLM 边界，与 test_consolidation 既有口径一致）；
臂 3 真校验→真调用方 reject 路径（不触网）；臂 4 纯静态 AST 扫描。
"""

import ast
import logging
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from lantai.api.app import app
from lantai.core.settings import settings
from lantai.models.tables import MemoryItem

# ── 臂 1：磨勘 query 原话整条进日志（P2，REST %0A 伪造行 + 无截断）────────


_FORGED_MARK = "2026-11-07 ERROR 伪造日志行"
_LONG_TAIL = "TAILMARK超窗尾段落入口志"  # >50 字窗之外的截断探针


def test_mokan_audit_log_no_forged_line_and_truncated(param_env, caplog):
    """红：GET /mokan/audit?query=x%0A<伪造行> —— 磨勘日志含真换行（独立
    伪造行注入共享日志流）且超长 query 无截断。修复后：换行转义为字面量、
    query 截断到 50 字窗内（票面修复目标 1：改记 query[:50]）。"""
    client = TestClient(app)
    evil_query = "x\n" + _FORGED_MARK + "\n" + "很" * 60 + _LONG_TAIL
    with caplog.at_level(logging.INFO, logger="lantai"):
        r = client.get(
            "/mokan/audit",
            params={"query": evil_query, "limit": 5},
            headers={"X-API-Key": "test"},
        )
    assert r.status_code == 200, f"真 app 磨勘路由应 200（红必须红在日志病灶而非 5xx）：{r.status_code}"
    hits = [rec.getMessage() for rec in caplog.records if "磨勘" in rec.getMessage()]
    assert hits, "磨勘审计完成日志应存在（真链路直跑）"
    line = hits[-1]
    assert "\n" not in line, f"日志记录含真换行——攻击者可伪造独立日志行：{line!r}"
    assert _FORGED_MARK not in line, f"伪造行标记整段进日志：{line!r}"
    assert _LONG_TAIL not in line, f"query 超 50 字窗未截断：{line!r}"


# ── 臂 2：沉潜成功日志携带记忆正文片段（P2）───────────────────────────────


_PURIFIED = {
    "consolidated_content": "大哥偏好大佛龙井\n冲泡85度明前茶",
    "importance": 0.9,
    "confidence": 0.95,
}


def test_consolidation_success_log_no_content(param_env, caplog):
    """红：真库折叠一簇（off 直写路径，仅替 LLM 提纯段）——成功日志带
    content[:30] 正文片段（含换行顺带注入）。修复后只记 master.id 与
    簇大小/长度，正文零落日志（票面修复目标 2）。"""
    with param_env[0]() as s:
        frags = [
            MemoryItem(
                id=f"mem_t50_{i}",
                content=f"大哥今天早上泡了明前大佛龙井茶第{i}杯",
                lane="preference",
                domain="user",
                decay_score=0.9,
                status="active",
            )
            for i in range(3)
        ]
        s.add_all(frags)
        s.commit()

        from lantai.services.consolidation_service import consolidate_cluster

        with patch(
            "lantai.services.consolidation_service.chat_json",
            return_value=dict(_PURIFIED),
        ), caplog.at_level(logging.INFO, logger="lantai"):
            master = consolidate_cluster(cluster_items=frags, session=s)

    assert master is not None, "off 直写路径应产出主记忆（链路真跑前提）"
    hits = [rec.getMessage() for rec in caplog.records if "沉潜：成功将" in rec.getMessage()]
    assert hits, "沉潜成功日志应存在（真折叠链路直跑）"
    line = hits[-1]
    assert master.id in line, f"成功日志应含 master.id（排障锚点）：{line!r}"
    assert "大佛龙井" not in line, f"记忆正文片段进日志（第二份无生命周期留存）：{line!r}"
    assert "\n" not in line, f"正文换行顺带注入日志：{line!r}"


# ── 臂 3：validate_api_url 异常文本携带完整 URL（P3，key-in-URL 泄密钥）──


def test_api_url_reject_log_has_no_secret(caplog, monkeypatch):
    """红：key-in-URL 形态（Gemini 式 ?key=）的非 https BASE_URL 触发
    validate_api_url 拒收 → reranker 调用方 warning 透传完整 URL → 密钥
    入日志。修复后异常 message 只带 parsed.scheme/host（票面修复目标 3）。"""
    from lantai.retrieval import reranker as reranker_mod

    secret = "SECRETVALUE_t50"
    monkeypatch.setattr(settings, "RERANKER_BASE_URL", f"http://api.openai.com/v1?key={secret}")
    with caplog.at_level(logging.WARNING, logger="lantai"):
        out = reranker_mod.rerank("查询", ["文档一", "文档二"], top_k=2)
    assert out == [], "拒收路径应返回空列表降级（真调用方链路）"
    msgs = [rec.getMessage() for rec in caplog.records]
    assert any("rejected" in m for m in msgs), f"BASE_URL rejected 日志应存在：{msgs}"
    joined = "\n".join(msgs)
    assert secret not in joined, f"密钥整条进日志（key-in-URL 形态）：{joined!r}"
    assert "key=" not in joined, f"URL 凭证参数形态进日志：{joined!r}"


# ── 臂 4：哨兵——logger 插值不得直用用户可控原文变量（AST 全仓扫描）──────

# 票 50 锁定面：用户可控/宿主透传原文变量名。经 loggable() 包装即合规。
# （review 加固：event_id/stripped——REST 回执体与 URL 剥边别名同属原文形态）
_FORBIDDEN_NAMES = {
    "query",
    "clean_q",
    "content",
    "raw_text",
    "combined_content",
    "combined",
    "url",
    "stripped",
    "sid",
    "session_id",
    "request_id",
    "event_id",
    "key",
}

# 白名单（file 前缀, message 首参前缀）：长度型/内部值合法位点，逐条登记理由。
# AST 形状规则先行：len(...) 包裹恒返回 int（长度标量，非原文）——不需白名单。
# review 收窄：白名单条目须精确到具体 message（前缀不得宽到「任意 獬豸 开头
# 消息」，那会把未来的原文插值也豁免掉）；新增条目须附理由。
_WHITELIST = set()


def _logger_call_violations():
    import lantai

    pkg_root = Path(lantai.__file__).parent
    violations = []
    for py in sorted(pkg_root.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        rel = py.relative_to(pkg_root).as_posix()
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"debug", "info", "warning", "error", "exception", "critical"}
            ):
                continue
            # review 加固：接收者不限于裸名 logger——logging.getLogger(__name__)
            # 内联形态与别名变量同在锁定面
            recv = node.func.value
            if isinstance(recv, ast.Name):
                if recv.id != "logger":
                    continue
            elif isinstance(recv, ast.Call) and isinstance(recv.func, ast.Attribute):
                if recv.func.attr != "getLogger":
                    continue
            else:
                continue
            for arg in node.args:
                # 修复形态：参数整体经 loggable() 包装 → 合规
                if (
                    isinstance(arg, ast.Call)
                    and isinstance(arg.func, ast.Name)
                    and arg.func.id == "loggable"
                ):
                    continue
                # 长度标量：len(...) 恒返回 int，非原文插值 → 合规
                if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) and arg.func.id == "len":
                    continue
                for sub in ast.walk(arg):
                    if isinstance(sub, ast.Name) and sub.id in _FORBIDDEN_NAMES:
                        first = node.args[0] if node.args else None
                        msg = (
                            first.value
                            if isinstance(first, ast.Constant) and isinstance(first.value, str)
                            else "<dynamic>"
                        )
                        if any(
                            rel == wfile and msg.startswith(wmsg) for wfile, wmsg in _WHITELIST
                        ):
                            continue
                        violations.append(f"{rel}:{node.lineno} name={sub.id!r} msg={msg!r}")
    return violations


def test_logger_interpolation_sentinel():
    """红：全仓 logger 调用参数表达式直用 query/content/url/key 等原文变量。

    白名单登记长度型合法位点（防回潮哨兵，票面 TDD 要求 4）；修复后八处
    位点全部经 loggable() 包装，哨兵转绿。"""
    violations = _logger_call_violations()
    assert not violations, (
        "logger 插值直用用户可控原文变量（须 loggable() 包装或白名单登记）：\n"
        + "\n".join(violations)
    )


# ── AGENTS.md 测试纪律：loggable 本体不 mock 冒烟（review 发现 6）──────────


def test_loggable_smoke_real_inputs():
    """核心 helper 直调冒烟（不 mock）：单行化、控制符转义、截断、keep_tail
    真尾部、None/非字符串输入、有界性（超长输入不产生超长输出）。"""
    from lantai.core.logger import loggable

    # 单行化：换行统一转义为 \n 字面量（\r、\r\n 同口径）——不再是真换行
    assert loggable("a\nb\rc\r\nd") == "a\\nb\\nc\\nd"
    assert "\n" not in loggable("x\ny")
    # 控制符 → \xNN；\t 保留
    assert loggable("a\x00b\x1fc\td") == "a\\x00b\\x1fc\td"
    # 截断：默认保头 + …
    assert loggable("很" * 60, limit=10) == "很" * 9 + "…"
    assert len(loggable("很" * 60, limit=10)) <= 10
    # keep_tail：真尾部语义（review 发现 1——此前实现保头，语义反转）
    out = loggable("HEADTAIL" + "x" * 50, limit=10, keep_tail=True)
    assert out.startswith("…"), f"keep_tail 应以前导 … 标示截头：{out!r}"
    assert "HEADTAIL" not in out, f"keep_tail 应保留尾部而非头部：{out!r}"
    assert len(out) <= 10
    # URL 形态：尾部 query 参数位在 keep_tail 下可见
    url_out = loggable("https://e.co/f?token=SECRETVALUE", limit=20, keep_tail=True)
    assert "SECRETVALUE" in url_out
    # limit=None：只单行化不截断
    assert loggable("a\nb" * 100, limit=None) == "a\\nb" * 100
    # None → ""；非字符串 → str() 后同口径
    assert loggable(None) == ""
    assert loggable(ValueError("boom\nline2")) == "boom\\nline2"
    # 有界性：5 MB 输入 + 20 字日志位 → 输出恒 ≤20
    big = "很" * 250_000
    assert len(loggable(big, limit=20)) <= 20
    assert len(loggable(big, limit=20, keep_tail=True)) <= 20
