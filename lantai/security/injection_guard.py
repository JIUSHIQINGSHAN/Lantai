"""獬豸（Xiezhi，写入侧注入检测）：记忆入库前的三层注入检测闸（G5 第二刀）。

上游 aiduMEM f0.3++ `ducky/security/injection_guard.py` 检测层同款移植，
模式表抄的是他们外审修正后的版本——三个审计教训都烧在模式里：

- P1-B（误拒）：裸 `<system>` 是 XML 语法、`[section]` 是 INI、7 行表格分隔
  是任何表格的组成部分——方括号/尖括号类**必须带指令词共现**才命中；
  重复行轰炸阈值 count>=10 且 ratio>0.6，先剔除结构性重复行。
- A11（绕过）：全角 `ｉｇｎｏｒｅ` 若先删非 ASCII 字符会整段蒸发——必须
  先 NFKC 折回半角，归一化匹配才有效。
- F-12（边界哲学）：检测总有绕法，出口边界靠编码不靠检测——所以本模块
  只做确定性底座，出口侧仍由樊篱（`lantai/llm/fence.py`）包裹，一进一出互补。

与上游的分歧（票据 .scratch/xiezhi-injection-guard/01 设计裁决）：
- 只检测不改写：命中整条拒（宁 miss 不脏写），不清洗不截断——截断会把
  攻击者可控的后缀留在判定之外；
- 裸 `dan` 从角色劫持模式中剔除（`dan mode` 保留）——「扮演 Dan」是普通人名
  角色扮演，中文对抗形态已被「无限制/不受约束」覆盖，误伤面不值当。
"""

from __future__ import annotations

import logging
import re
import unicodedata

from lantai.core.settings import settings

logger = logging.getLogger("lantai.security")

# 预算边界：入库内容超过此长度不再逐层扫描，直接按超限拒收。与最宽松的
# 既有入口上限（obsidian verbatim 200k）对齐，只对绕过入口直调本模块的
# 调用方构成实际约束。
MAX_CONTENT_CHARS = 200_000

# ── 第一层：原始特征检测（指令覆盖 / 角色劫持 / ChatML 标记）──────────────
# 全部中文相邻组共享字面量，量词一律 {0,8} 有界——近匹配不能在两组之间劈开
# 长重复做二次回溯（上游独立进程预算验证过 32KiB/100KiB near-match）。
_RAW_INJECTION_PATTERNS = re.compile(
    # 英文指令覆盖
    r"ignore\s+(all\s+)?(your\s+)?(previous|prior|earlier|above|system)\s+(instructions?|directions?|prompts?)"
    r"|forget\s+(all\s+|everything\s+)?(you\s+)?(learned|were\s+told|remember)\s+(about\s+your\s+rules|and\s+start\s+fresh)"
    r"|disregard\s+(all\s+)?(previous\s+|prior\s+)?(instructions?|commands?|directives?|system\s+prompts?)"
    r"|do\s+not\s+follow\s+(the\s+|any\s+|these\s+)?(instructions?|system\s+prompts?)"
    r"|you\s+must\s+(ignore|forget|override|bypass)\s+(all\s+)?(rules?|instructions?|system\s+prompts?)"
    r"|override\s+(all\s+)?(system\s+)?(prompts?|instructions?)"
    # 英文角色劫持（限定对抗/越狱形态；裸 dan 剔除——人名误伤，见模块 docstring）
    r"|(from\s+now\s+on\s+you\s+are|act\s+as|pretend\s+(you\s+are|to\s+be)|you\s+are\s+now)\s+(an?\s+)?(unrestricted|jailbroken|dan\s+mode|developer\s+mode|evil|god\s+mode|bypass\s+mode)"
    r"|your\s+(new|real|true|actual)\s+system\s+(prompt|instruction)\s+is"
    # 系统级标记与特殊 Token：管道符必需——裸 <system> 是 XML，agent 最常存
    r"|<\|?im_start\|?>|<\|?im_end\|?>|<\|?endoftext\|?>"
    r"|<\|\s*(system|user|assistant)\s*\|>"
    # 方括号/尖括号类必须词组共现（裸 [system] / <system> 放行）
    r"|\[\s*(system|assistant|developer)\s+(prompt|message|instruction)s?\s*\]"
    r"|\[/?\s*(system|assistant|developer)\s+(prompt|message|instruction)s?\s*\]"
    r"|<\s*(?:/\s*)?(system|assistant|developer)\s+(prompt|message|instruction)s?\s*>"
    # 中文指令覆盖与角色劫持
    r"|忽略(之前|先前|上述|上面|历史|原有|所有|全部){0,8}(的)?(所有|全部|之前|先前|历史){0,8}(系统)?(指令|指示|提示词)"
    r"|忘记(所有|一切|你学到的|你的记忆){0,8}(的)?(系统)?(指令|提示词)"
    r"|从现在(起|开始)?(你(是|将是)|扮演|假装)(无限制|越狱|DAN|不受约束)"
    r"|你现在的真实(系统)?(指令|提示词)是"
    r"|你的真实(系统)?(指令|提示词)是"
    r"|不要遵守(上述|任何|这些|系统)?(系统)?(指令|提示词)"
    r"|覆盖(系统)?(指令|提示词)"
    r"|扮演无限制|无视(道德|安全|系统)?限制",
    re.IGNORECASE | re.DOTALL,
)

# ── 第二层：归一化字符去重正则（粉碎 i.g.n.o.r.e / 忽 略 指 令 等变体）──────
_NORMALIZE_CLEAN_RE = re.compile(r"[^0-9a-zA-Z一-鿿]", re.UNICODE)
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_NORMALIZED_INJECTION_PATTERNS = re.compile(
    r"ignore(all)?(your)?(previous|prior|earlier|above|system)?(instruction|instructions|direction|prompt)"
    r"|forget(all|everything)?(you)?(learned|weretold|remember)?(system)?(instruction|prompt)"
    r"|disregard(all)?(previous|prior)?(instruction|command|directive|systemprompt)"
    r"|(fromnowonyouare|actas|pretendto|youarenow)(unrestricted|jailbroken|danmode|developermode|evil)"
    r"|youmust(ignore|forget|override|bypass)(all)?(rule|instruction|systemprompt)"
    r"|override(all)?(system)?(prompt|instruction)"
    r"|忽略(之前|先前|上述|上面|历史|原有|所有|全部){0,8}(的)?(所有|全部|之前|先前|历史){0,8}(系统)?(指令|指示|提示词)"
    r"|忘记(所有|一切|你学到的|你的记忆){0,8}(的)?(系统)?(指令|提示词)"
    r"|从现在(起|开始)?(你是|扮演|假装)(无限制|越狱|danmode|不受约束)|扮演无限制|无视(道德|系统|安全)?限制"
    r"|你的真实(系统)?(指令|提示词)是"
    r"|不要遵守(上述|任何|这些)?(系统)?(指令|提示词)",
    re.IGNORECASE,
)

# ── 第三层：重复行轰炸（识别大篇幅填充）────────────────────────────────────
# 先剔除结构性重复（表格分隔行、纯标点行）再统计；阈值 count>=10 且
# ratio>0.6——3 行相同 ERROR 是真实日志的常态，攻击要的是把上下文挤爆。
_STRUCTURAL_LINE_RE = re.compile(r"^[\s|:+_=~*#.\-]*$")


def check_prompt_injection(content: str) -> tuple[bool, str]:
    """三层检测判断是否存在提示注入风险。返回 (是否命中, 层名)。

    纯函数：不抛异常、不改写内容，只做判定。层名：MAX_LENGTH / Layer 1 /
    Layer 2 / Layer 3。
    """
    if not isinstance(content, str) or not content:
        return False, ""

    # 预算边界：本函数也是公共直调边界，不依赖漏斗先做限长
    if len(content) > MAX_CONTENT_CHARS:
        return True, "MAX_LENGTH"

    # 1. 原始正则匹配
    if _RAW_INJECTION_PATTERNS.search(content):
        return True, "Layer 1"

    # 2. NFKC 折叠 + 控制字符清洗 + 去标点后的归一化匹配
    #    必须先 NFKC：全角字母若直接被去标点正则删光，攻击变体就蒸发绕过
    folded = _CONTROL_CHARS_RE.sub("", unicodedata.normalize("NFKC", content))
    if _RAW_INJECTION_PATTERNS.search(folded):
        return True, "Layer 2"
    normalized = _NORMALIZE_CLEAN_RE.sub("", folded).lower()
    if len(normalized) >= 4 and _NORMALIZED_INJECTION_PATTERNS.search(normalized):
        return True, "Layer 2"

    # 3. 重复行轰炸检测
    lines = [
        ln.strip().lower()
        for ln in folded.split("\n")
        if ln.strip() and not _STRUCTURAL_LINE_RE.match(ln.strip())
    ]
    if len(lines) > 6:
        counts: dict[str, int] = {}
        for ln in lines:
            counts[ln] = counts.get(ln, 0) + 1
        count = max(counts.values())
        if count >= 10 and (count / len(lines)) > 0.6:
            return True, "Layer 3"

    return False, ""


def assert_no_prompt_injection(content: str) -> None:
    """獬豸闸：写入漏斗接线点。命中即 raise ValueError（整条拒，不改写）。

    INJECTION_GUARD_ENABLED=False 时直通（关 = 行为退回无检测）。拒绝信息
    只给层名不给命中模式全文——不给探测反馈。
    """
    if not settings.INJECTION_GUARD_ENABLED:
        return
    hit, layer = check_prompt_injection(content)
    if hit:
        logger.warning("獬豸 REJECTED layer=%s chars=%s", layer, len(content or ""))
        raise ValueError(f"content rejected by injection guard (layer={layer})")
