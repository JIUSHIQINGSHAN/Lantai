import logging
import sys

logger = logging.getLogger("lantai")


def loggable(text, limit: int | None = 200, keep_tail: bool = False) -> str:
    """日志单行化 + 截断 helper（票 50，单一真源）。

    用户可控/宿主透传值（query、content、url、session_id、幂等 key、
    request_id 等）直落日志的两个病灶：换行/控制符把共享日志流撕成多行
    （攻击者可用 REST 参数里的 %0A 伪造独立日志行），以及无界长度让正文
    成为日志里的第二份无生命周期留存。统一口径：

    1. 换行（\\n/\\r/\\r\\n）转义为字面量 ``\\n``，其余 C0/C1 控制符转义为
       ``\\xNN``——任何输入都压成单行；
    2. 超长按码点截断（复用 truncate_codepoints，不切 emoji 代理对），
       默认保留头部附 ``…``；``keep_tail=True`` 保留**尾部**附前导 ``…``
       （URL 排障要看尾部的 query 参数位）。

    非字符串输入原样 str() 后同样处理（SDK 异常对象透传位点直接包）。
    limit=None 表示只单行化不截断（调用方已有界时）。

    有界性（review 修复）：先按 limit 预切再转义——超长输入（宿主可控）
    的 O(n) 全串扫描收敛为 O(limit)，20 字日志位不陪葬 5 MB 查询的全串
    转义。预切 budget = limit - 1（留 1 位给省略号）；控制符转义的膨胀
    （1 码点 → 4 字面字符）由末端 truncate_codepoints 收口回 limit。
    """
    from lantai.core.text import truncate_codepoints

    if text is None:
        return ""
    s = text if isinstance(text, str) else str(text)
    # 有界预切：keep_tail 留尾（前导 … 标示截头），默认保头（尾随 …）
    if limit is not None and limit > 0:
        budget = max(limit - 1, 1)
        if len(s) > budget:
            s = ("…" + s[-budget:]) if keep_tail else (s[:budget] + "…")
    s = s.replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n")
    s = "".join(
        f"\\x{ord(ch):02x}" if (ord(ch) < 32 and ch != "\t") or 0x7F <= ord(ch) <= 0x9F else ch
        for ch in s
    )
    if limit is None:
        return s
    return truncate_codepoints(s, limit, "…" if not keep_tail else "")


if not logger.handlers:
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(h)
    logger.setLevel(logging.INFO)
