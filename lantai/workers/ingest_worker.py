from sqlmodel import select

from lantai.core import scheduler as scheduler_mod
from lantai.core.ids import new_id
from lantai.core.logger import logger
from lantai.core.provenance import PROVENANCE_PROMPT_EXTRACT, make_provenance
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.ingestion.registry import ADAPTERS
from lantai.models.tables import IngestJob, MemoryCandidate, RawDocument, Source
from lantai.parsing.extractor import extract_candidate
from lantai.security.injection_guard import assert_no_prompt_injection
from lantai.security.secret_guard import detect_secret_shapes, route_secret_hits
from lantai.storage import db


def run_ingest_once():
    with db.get_session() as s:
        sources = s.exec(select(Source).where(Source.enabled)).all()
    for src in sources:
        job = IngestJob(id=new_id("job"), source_id=src.id, status="running", started_at=utcnow())
        with db.get_session() as s:
            s.add(job)
            s.commit()
            s.refresh(job)

        try:
            adapter = ADAPTERS[src.kind]
            docs = adapter.fetch(src.config)
            new_docs, new_cands, blocked = 0, 0, 0
            paper_doc_ids: list[str] = []
            secret_hits_by_cand: list[tuple[str, list[str]]] = []
            with db.get_session() as s:
                for d in docs:
                    exists = s.exec(
                        select(RawDocument).where(RawDocument.content_hash == d.content_hash)
                    ).first()
                    if exists:
                        continue
                    s.add(d)
                    s.flush()
                    new_docs += 1

                    # 闸文本（獬豸 + 密钥共用一个组合口径）：title 与 content 以换行
                    # 分隔——相邻拼接会让 title 尾词与 content 首词融出第四层匹配面。
                    gate_text = f"{d.title}\n{d.content}"

                    # 獬豸（v0.68 票 03）：外部内容摄取面是唯一无写入闸入口——RSS/arxiv
                    # 是第三方可写内容，注入载荷经提取进入候选、晋升为 MemoryItem 后经
                    # 完璧/verbatim 等注入面回到模型上下文。命中即跳过该 doc 的候选创建
                    # （宁 miss 不脏写）：RawDocument 行可留 + meta 记阻断标记供审计
                    # （闸位前已 flush，JSON 列必须整属性重赋，原地改不被 dirty 跟踪，
                    # 且 arxiv meta 已携 quality_signal 须合并不覆盖）。
                    # 逐 doc 捕获：闸异常若逸出会回滚同事务整批（含正常 doc），「一个
                    # 恶意 feed 不得拖停」在 source 内同样要成立。
                    try:
                        assert_no_prompt_injection(gate_text)
                    except ValueError as exc:
                        d.meta = {**d.meta, "xiezhi_blocked": True}
                        blocked += 1
                        logger.warning("獬豸阻断摄取候选 doc=%s src=%s：%s", d.id, src.id, exc)
                        continue

                    # 密钥熔断（X-06 同口径）：off 不检测；shadow 检测+记录不阻断；
                    # enforce 命中候选转待审（route_secret_hits 单一真源，不静默丢弃）。
                    secret_hits = detect_secret_shapes(gate_text)

                    # 参数建议：仅 paper 类型入队（RSS article 首期不参与）
                    if d.source_type == "paper":
                        paper_doc_ids.append(d.id)

                    data = extract_candidate(d.title, d.content)
                    cand = MemoryCandidate(
                        id=new_id("cand"),
                        document_id=d.id,
                        topic=data["topic"],
                        summary=data["summary"],
                        claims=data["claims"],
                        methods=data["methods"],
                        constraints=data["constraints"],
                        actions=data["actions"],
                        extractor_confidence=data["extractor_confidence"],
                        provenance=make_provenance(PROVENANCE_PROMPT_EXTRACT),
                    )
                    s.add(cand)
                    new_cands += 1
                    if secret_hits:
                        secret_hits_by_cand.append((cand.id, secret_hits))
                s.commit()

            # 密钥 enforce 路由（X-06 单一真源）：在摄入事务提交**之后**执行——
            # enqueue_rejected 自开 session，事务内读不到未提交行，静默 no-op
            # （同 paper 入队的既有形态）；shadow/off 由 route_secret_hits 原样
            # 返回，零副作用。逐项容错对齐 paper 入队：单项失败留痕不拖累其余
            # 路由，也不把已提交的批次改判 failed。
            for cand_id, hits in secret_hits_by_cand:
                try:
                    route_secret_hits({"candidate_id": cand_id}, hits)
                except Exception:
                    logger.exception("密钥 enforce 路由失败 cand=%s hits=%s", cand_id, hits)

            # 论文已落库成功，幂等入队（在摄入事务提交后，避免持锁等网络）
            from lantai.parameters.queue import enqueue_paper_for_param_advice

            for pid in paper_doc_ids:
                try:
                    enqueue_paper_for_param_advice(pid)
                except Exception:
                    logger.exception("enqueue paper for param advice failed: %s", pid)

            # 质量信号落库（方向一）：doc.meta 中携带的解析草稿 → paper_quality_signal
            if paper_doc_ids and settings.PAPER_SIGNAL_ENABLED:
                try:
                    from lantai.models.tables import RawDocument as RD
                    from lantai.parameters.paper_signals import QualitySignalDraft
                    from lantai.parameters.signal_service import upsert_from_draft

                    with db.get_session() as s:
                        docs = s.exec(select(RD).where(RD.id.in_(paper_doc_ids))).all()
                        drafts = {d.id: d.meta.get("quality_signal") for d in docs}
                    for pid, payload in drafts.items():
                        if payload:
                            upsert_from_draft(pid, QualitySignalDraft.model_validate(payload))
                        else:
                            # 无草稿（解析失败）→ 保底 tier D
                            upsert_from_draft(pid, QualitySignalDraft(arxiv_id=pid))
                except Exception:
                    logger.exception("quality signal upsert failed")

            with db.get_session() as s:
                # L6（票 16）：job 行可能已被并发删除（外部清理/另一进程），
                # 不得 None 解引用——按「任务已不存在」留痕跳过收尾。
                # 变量名不覆盖内存态 job（except 路径仍需 job.id 留痕）。
                done_job = s.get(IngestJob, job.id)
                if done_job is None:
                    logger.warning("ingest job %s 任务已不存在（并发删除），跳过收尾", job.id)
                else:
                    done_job.status = "done"
                    done_job.finished_at = utcnow()
                    done_job.stats = {"docs": new_docs, "candidates": new_cands, "blocked": blocked}
                    src2 = s.get(Source, src.id)
                    src2.last_fetched_at = utcnow()
                    s.add(done_job)
                    s.add(src2)
                    s.commit()
            logger.info("ingest src=%s docs=%d cand=%d", src.id, new_docs, new_cands)
        except Exception as e:
            logger.exception("ingest failed for %s", src.id)
            with db.get_session() as s:
                # L6（票 16）：同上——失败收尾同样防护（此处若炸会传播出
                # worker 中断整个 source 循环，后续 source 全部停摆）。
                failed_job = s.get(IngestJob, job.id)
                if failed_job is None:
                    logger.warning("ingest job %s 任务已不存在（并发删除），跳过失败记录", job.id)
                else:
                    failed_job.status = "failed"
                    failed_job.error = str(e)
                    failed_job.finished_at = utcnow()
                    s.add(failed_job)
                    s.commit()

    # 本轮摄入提交后触发一次参数建议（窗口不满足则空转）
    try:
        from lantai.workers.param_advice_worker import run_param_advice_once

        run_param_advice_once()
    except Exception:
        logger.exception("param advice trigger failed")
    scheduler_mod.record_run("ingest")


def run_coalesce_idle():
    """coalesce 空闲冲刷消费：冲刷出的批量内容持久化，不静默丢弃。"""
    from lantai.ingestion.coalesce import get_coalesce_buffer
    from lantai.models.schemas import AddMemoryReq
    from lantai.services import memory_service as ms

    buffer = get_coalesce_buffer()
    for result in buffer.check_idle():
        if not result.get("flushed"):
            continue
        combined = result.get("combined_content", "")
        key = result.get("key", "default:general:")
        # 会话分键（票据 .scratch/coalesce-session-key/01）：键尾部两段 =
        # lane:session（user_id 可能含冒号，rsplit 解析；_flush 已带回解析结果）
        lane = result.get("lane") or key.rsplit(":", 2)[-2]
        session_id = result.get("session_id") or ""
        try:
            req = AddMemoryReq(
                title="coalesced", content=combined, lane=lane, session_id=session_id
            )
            ms._create_candidate_with_extraction(req)
        except Exception:
            logger.exception("coalesce flush persist failed for key=%s", key)
            # 持久化失败：锁内恢复该批消息（不触发二次冲刷），下轮再试
            buffer.requeue(key, result.get("items", []))
    scheduler_mod.record_run("coalesce")
