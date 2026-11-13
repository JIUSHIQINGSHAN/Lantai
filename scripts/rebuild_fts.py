"""票 45：FTS 索引存量全量重建（一次性运维脚本，幂等）。

归一缝（lantai/core/text.py::normalize_for_index）上线前的存量索引是未归一
原文：全角/ZWSP/NFD 正文与归一查询确定性失配（票 45 病灶 1/2）。FTS 索引
是派生数据——truncate 后从 memoryitem 逐行重灌（sync_fts 同款归一），正文
零触碰。重复执行无副作用。

用法：.venv/Scripts/python.exe scripts/rebuild_fts.py
"""

import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lantai.storage import db  # noqa: E402
from lantai.storage.fts import rebuild_fts  # noqa: E402

with db.get_session() as session:
    n = rebuild_fts(session)
    session.commit()
print(f"memory_fts rebuilt: {n} rows indexed")
