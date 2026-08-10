"""分层回填脚本——从 RawDocument 重新跑 extractor + gate + evolution

默认 dry-run，--apply 才真正执行
"""
import sys
from sqlmodel import select

from lantai.models.tables import RawDocument, MemoryCandidate
from lantai.storage import db
from lantai.parsing.extractor import extract_candidate
from lantai.core.ids import new_id


def reextract(apply: bool = False) -> dict:
    """从 RawDocument 重新跑提取链路。"""
    stats = {"total_docs": 0, "reextracted": 0, "skipped": 0, "errors": 0}

    with db.get_session() as s:
        docs = s.exec(select(RawDocument)).all()
        stats["total_docs"] = len(docs)

        for doc in docs:
            try:
                # 检查是否已有 candidate
                existing = s.exec(
                    select(MemoryCandidate)
                    .where(MemoryCandidate.document_id == doc.id)
                ).first()

                if existing and not apply:
                    stats["skipped"] += 1
                    continue

                data = extract_candidate(doc.title, doc.content)
                if apply:
                    cand = MemoryCandidate(
                        id=new_id("cand"), document_id=doc.id,
                        topic=data["topic"],
                        summary=data["summary"],
                        claims=data["claims"], methods=data["methods"],
                        constraints=data["constraints"], actions=data["actions"],
                        extractor_confidence=data["extractor_confidence"],
                    )
                    s.add(cand)
                stats["reextracted"] += 1
            except Exception as e:
                stats["errors"] += 1
                print(f"  ERROR doc {doc.id}: {e}")

        if apply:
            s.commit()

    mode = "APPLIED" if apply else "DRY-RUN"
    print(f"\nReextract ({mode}): {stats}")
    return stats


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    reextract(apply=apply)
