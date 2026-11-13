"""分层回填脚本——给还没有候选的 RawDocument 补跑提取，建出候选

默认 dry-run（只统计、不写库、不调 LLM），--apply 才真正执行。

写入口径与摄取入口 `lantai/workers/ingest_worker.py::run_ingest_once` 对齐：

- **只回填、不重复**：已有任何候选的 doc 一律跳过（dry-run 与 --apply 同口径）。
  dialogue / 手动记忆这类 doc 在落库时就建过候选（可能已在待审、已被拒），
  重跑一次再建一条 status=new 的候选，等于把待审/已拒的内容重新送进晋升链路。
- **过獬豸闸**：`meta["xiezhi_blocked"]` 已记阻断的 doc 直接跳过（票 03 特意留下
  这些行供审计，不是留给回填的）；没记标记的 doc 按摄取同口径（title + 换行 +
  content）再过一次 `assert_no_prompt_injection`，命中则跳过，--apply 时补记标记。
- **密钥熔断**：`detect_secret_shapes` 命中的候选在事务提交后交给
  `route_secret_hits`（off/shadow/enforce 三态单一真源）。
- **溯源**：候选带 `make_provenance(PROVENANCE_PROMPT_EXTRACT)`，与摄取同款。
"""

import sys

from sqlmodel import select

from lantai.core.ids import new_id
from lantai.core.provenance import PROVENANCE_PROMPT_EXTRACT, make_provenance
from lantai.models.tables import MemoryCandidate, RawDocument
from lantai.parsing.extractor import extract_candidate
from lantai.security.injection_guard import assert_no_prompt_injection
from lantai.security.secret_guard import detect_secret_shapes, route_secret_hits
from lantai.storage import db


def reextract(apply: bool = False) -> dict:
    """给没有候选的 RawDocument 补跑提取链路。

    stats 口径（dry-run 与 --apply 一致，dry-run 的 reextracted 即「--apply 会建几条」）：
    - skipped：已有候选，跳过
    - blocked：獬豸已记阻断或本次重闸命中，跳过
    - reextracted：建了（dry-run 下为将建）候选
    - errors：单 doc 异常
    """
    stats = {"total_docs": 0, "reextracted": 0, "skipped": 0, "blocked": 0, "errors": 0}
    secret_hits_by_cand: list[tuple[str, list[str]]] = []

    with db.get_session() as s:
        docs = s.exec(select(RawDocument)).all()
        stats["total_docs"] = len(docs)

        for doc in docs:
            try:
                existing = s.exec(
                    select(MemoryCandidate).where(MemoryCandidate.document_id == doc.id)
                ).first()
                if existing:
                    stats["skipped"] += 1
                    continue

                meta = doc.meta or {}
                if meta.get("xiezhi_blocked"):
                    stats["blocked"] += 1
                    continue

                # 与 ingest_worker 同一闸文本：title 与 content 以换行分隔
                gate_text = f"{doc.title}\n{doc.content}"
                try:
                    assert_no_prompt_injection(gate_text)
                except ValueError as exc:
                    stats["blocked"] += 1
                    print(f"  BLOCKED doc {doc.id}: {exc}")
                    if apply:
                        # JSON 列须整属性重赋，原地改不被 dirty 跟踪
                        doc.meta = {**meta, "xiezhi_blocked": True}
                        s.add(doc)
                    continue

                if not apply:
                    # dry-run 只统计：不调 LLM、不写库
                    stats["reextracted"] += 1
                    continue

                secret_hits = detect_secret_shapes(gate_text)
                data = extract_candidate(doc.title, doc.content)
                cand = MemoryCandidate(
                    id=new_id("cand"),
                    document_id=doc.id,
                    topic=data["topic"],
                    summary=data["summary"],
                    claims=data["claims"],
                    methods=data["methods"],
                    constraints=data["constraints"],
                    actions=data["actions"],
                    extractor_confidence=data["extractor_confidence"],
                    provenance=make_provenance(
                        PROVENANCE_PROMPT_EXTRACT, extra={"origin_source": "reextract"}
                    ),
                )
                s.add(cand)
                stats["reextracted"] += 1
                if secret_hits:
                    secret_hits_by_cand.append((cand.id, secret_hits))
            except Exception as e:
                stats["errors"] += 1
                print(f"  ERROR doc {doc.id}: {e}")

        if apply:
            s.commit()

    # 密钥 enforce 路由须在提交之后：enqueue_rejected 自开 session，读不到未提交行
    for cand_id, hits in secret_hits_by_cand:
        try:
            route_secret_hits({"candidate_id": cand_id}, hits)
        except Exception as e:
            stats["errors"] += 1
            print(f"  ERROR secret routing cand {cand_id}: {e}")

    mode = "APPLIED" if apply else "DRY-RUN"
    print(f"\nReextract ({mode}): {stats}")
    return stats


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    reextract(apply=apply)
