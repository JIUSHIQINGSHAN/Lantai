"""数值域钳制单一真源（票 35）。

LLM 提取与提案 patch 的 importance/confidence 是「外部输入的浮点」，写面散点
钳制必然漂移（本票首版就在两处手写 min/max，NaN 安全纯属实参顺序巧合——
max(nan, 0.0)=0.0 而 max(nan, 1.0)=nan，谁在前谁说了算）。域规则与 NaN 语义
收敛到本模块一处，越界/非数值/非有限三臂同源。
"""

import math

from lantai.core.logger import logger


def clamp_unit_interval(value, *, default: float) -> float:
    """把外部面的 importance/confidence 钳入 [0, 1]。

    - 越界可数值 → 钳入界内并 warning 留痕（票 35「钳入界内」口径：内容已过
      上游验证，为一个标量丢弃整簇折叠得不偿失）；
    - 非数值（str/None/容器）与非有限值（NaN/±inf）→ 回退 default 并 warning
      留痕——「视同缺省」，与 patch.get("importance", default) 的 missing 语义
      一致，不让 float() 抛穿调用链（apply 面抛穿 = run_pending_proposals 每
      轮重试同批毒提案的 livelock）；
    - 合法界内值原样返回，零日志零漂移。
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        logger.warning("数值面 [0,1] 钳制：非数值 %r 视同缺省回退 %r", value, default)
        return default
    if not math.isfinite(v):
        logger.warning("数值面 [0,1] 钳制：非有限值 %r 视同缺省回退 %r", v, default)
        return default
    clamped = min(1.0, max(0.0, v))
    if clamped != v:
        logger.warning("数值面 [0,1] 钳制：%r → %r", v, clamped)
    return clamped
