"""Store explicitly captured messages and their pending work in SQLite."""

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from re import compile as compile_pattern
from typing import Literal, Protocol
from uuid import uuid4

from sqlalchemy import Engine, and_, case, or_, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from lantai.core.ids import new_id
from lantai.core.time import iso_utc, utcnow
from lantai.models.tables import ConversationJob, ConversationMessage
from lantai.security.injection_guard import check_prompt_injection
from lantai.security.secret_guard import find_secret_shapes

_LABEL = compile_pattern(r"[A-Za-z0-9_.:-]{1,128}")
_SCOPE_COLUMNS = ("user_id", "tenant_id", "agent_id")
_SOURCE_COLUMNS = ("workspace_id", "host_id", "conversation_id", "source_message_id")
_PAYLOAD_FIELDS = frozenset((*_SOURCE_COLUMNS, "role", "completion", "content", "occurred_at"))
_ROLES = ("user", "assistant")
_COMPLETIONS = ("completed", "failed", "cancelled", "partial")
_MAX_ATTEMPTS = 3


class ConversationConflict(ValueError):
    """An existing message identity was submitted with a different payload."""


class ConversationPrincipal(Protocol):
    """Authenticated scope supplied by a trusted caller."""

    @property
    def user_id(self) -> str | None: ...

    @property
    def tenant_id(self) -> str | None: ...

    @property
    def agent_id(self) -> str | None: ...


def _identity_part(value: object, *, required: bool = False) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or len(value) > 128 or (required and not value.strip()):
        raise PermissionError("Conversation scope is invalid")
    if any(
        ord(char) < 32 or 127 <= ord(char) <= 159 or 0xD800 <= ord(char) <= 0xDFFF for char in value
    ):
        raise PermissionError("Conversation scope is invalid")
    return value


@dataclass(frozen=True, slots=True)
class _Scope:
    user_id: str
    tenant_id: str
    agent_id: str

    @classmethod
    def from_principal(cls, principal: ConversationPrincipal | None) -> "_Scope":
        if principal is None:
            raise PermissionError("Conversation scope is required")
        return cls(
            _identity_part(principal.user_id, required=True),
            _identity_part(principal.tenant_id),
            _identity_part(principal.agent_id),
        )

    def conditions(self) -> tuple:
        return (
            ConversationMessage.user_id == self.user_id,
            ConversationMessage.tenant_id == self.tenant_id,
            ConversationMessage.agent_id == self.agent_id,
        )

    def active_message_ids(self):
        return select(ConversationMessage.id).where(
            *self.conditions(), ConversationMessage.status == "stored"
        )


def _label(value: object, *, optional: bool = False) -> str:
    if optional and value == "":
        return ""
    if not isinstance(value, str) or not _LABEL.fullmatch(value):
        raise ValueError("Invalid conversation identifier")
    return value


def _utc(value: object) -> datetime:
    try:
        if isinstance(value, str) and len(value) <= 64:
            value = datetime.fromisoformat(value)
        if isinstance(value, datetime) and value.utcoffset() is not None:
            return value.astimezone(UTC)
    except (ValueError, OverflowError):
        pass
    raise ValueError("Conversation time must include a UTC offset")


def _now(now: datetime | None) -> datetime:
    return utcnow() if now is None else _utc(now)


def _message_fields(payload: Mapping[str, object]) -> dict:
    if not isinstance(payload, Mapping) or payload.keys() - _PAYLOAD_FIELDS:
        raise ValueError("Invalid conversation message fields")
    content = payload.get("content")
    if not isinstance(content, str) or len(content) > 50_000 or not content.strip():
        raise ValueError("Invalid conversation message text")
    try:
        content_hash = sha256(content.encode("utf-8")).hexdigest()
    except UnicodeEncodeError:
        raise ValueError("Invalid conversation message text") from None
    source_labels = {
        field: _label(payload.get(field, ""), optional=field == "workspace_id")
        for field in _SOURCE_COLUMNS
    }
    if any(find_secret_shapes(value) for value in (content, *source_labels.values())):
        raise ValueError("Conversation message contains a protected credential shape")
    role = payload.get("role")
    completion = payload.get("completion", "completed")
    if role not in _ROLES or completion not in _COMPLETIONS:
        raise ValueError("Invalid conversation role or completion")
    occurred_at = payload.get("occurred_at")
    injection_suspected, _ = check_prompt_injection(content)
    return {
        **source_labels,
        "role": role,
        "completion": completion,
        "content": content,
        "content_hash": content_hash,
        "occurred_at": _utc(occurred_at) if occurred_at is not None else None,
        "status": "quarantined" if injection_suspected else "stored",
    }


def _with_job():
    return select(ConversationMessage, ConversationJob).join(
        ConversationJob, ConversationJob.message_id == ConversationMessage.id
    )


def _receipt(message_id: str, status: str, job_status: str, *, replayed: bool) -> dict:
    return {
        "message_id": message_id,
        "status": status,
        "durable": status != "deleted",
        "replayed": replayed,
        "processing_status": job_status,
    }


def _message_view(message: ConversationMessage, job: ConversationJob) -> dict:
    return {
        "message_id": message.id,
        "host_id": message.host_id,
        "conversation_id": message.conversation_id,
        "source_message_id": message.source_message_id,
        "workspace_id": message.workspace_id,
        "role": message.role,
        "completion": message.completion,
        "content": message.content,
        "revision": 1,
        "occurred_at": iso_utc(message.occurred_at) or None,
        "received_at": iso_utc(message.received_at),
        "status": message.status,
        "processing_status": job.status,
    }


def _claim_conditions(scope: _Scope, job_id: str, token: str, current: float) -> tuple:
    return (
        ConversationJob.id == job_id,
        ConversationJob.status == "processing",
        ConversationJob.token == token,
        ConversationJob.lease_until > current,
        ConversationJob.message_id.in_(scope.active_message_ids()),
    )


class MessageStore:
    """Own the message and job transaction, idempotency, and scoped access."""

    def __init__(self, engine: Engine) -> None:
        # Keep the configured pool. Suppress SQL rows and parameters on this
        # engine only, even when the shared SQLAlchemy logger is in debug mode.
        self._engine = engine.execution_options()
        self._engine.hide_parameters = True
        self._engine.echo = False
        self._engine.logger = logging.Logger("lantai.conversation.sqlite", level=logging.WARNING)

    @contextmanager
    def _session(
        self, *, write_lock: Literal["IMMEDIATE", "EXCLUSIVE"] | None = None
    ) -> Iterator[Session]:
        try:
            with Session(self._engine) as session:
                if write_lock is not None:
                    # Lease time starts after waiting for the necessary locks.
                    begin = "BEGIN EXCLUSIVE" if write_lock == "EXCLUSIVE" else "BEGIN IMMEDIATE"
                    session.connection().exec_driver_sql(begin)
                yield session
        except SQLAlchemyError:
            # Do not expose request text through SQL exception parameters or causes.
            raise SQLAlchemyError("Conversation storage unavailable") from None

    def capture(
        self,
        principal: ConversationPrincipal | None,
        payload: Mapping[str, object],
        *,
        now: datetime | None = None,
    ) -> dict:
        """Commit the original message and its job together before acknowledging it."""
        scope = _Scope.from_principal(principal)
        fields = _message_fields(payload)
        received_at = _now(now)
        status = fields["status"]
        job_status = "held" if status == "quarantined" else "pending"
        message_id = new_id("msg")
        statement = (
            insert(ConversationMessage)
            .values(
                id=message_id,
                user_id=scope.user_id,
                tenant_id=scope.tenant_id,
                agent_id=scope.agent_id,
                received_at=received_at,
                **fields,
            )
            .on_conflict_do_nothing(index_elements=(*_SCOPE_COLUMNS, *_SOURCE_COLUMNS))
            .returning(ConversationMessage.id)
        )
        with self._session() as session:
            created_id = session.execute(statement).scalar_one_or_none()
            if created_id is not None:
                session.add(
                    ConversationJob(
                        id=new_id("job"),
                        message_id=created_id,
                        status=job_status,
                        reason_code="injection_suspected" if job_status == "held" else None,
                        created_at=received_at,
                    )
                )
                session.commit()
                return _receipt(created_id, status, job_status, replayed=False)

            source_match = tuple(
                getattr(ConversationMessage, name) == fields[name] for name in _SOURCE_COLUMNS
            )
            existing = session.exec(
                _with_job().where(*scope.conditions(), *source_match)
            ).one_or_none()
            if existing is None:
                raise SQLAlchemyError("Missing conversation processing record")
            message, job = existing
            compare = ("content_hash", "role", "completion", "occurred_at", "content")
            if message.status != "deleted" and any(
                getattr(message, name) != fields[name] for name in compare
            ):
                raise ConversationConflict("Conversation message identity conflicts")
            return _receipt(message.id, message.status, job.status, replayed=True)

    def get(self, principal: ConversationPrincipal | None, message_id: str) -> dict | None:
        """Read one message in the exact authenticated scope."""
        scope = _Scope.from_principal(principal)
        with self._session() as session:
            pair = session.exec(
                _with_job().where(*scope.conditions(), ConversationMessage.id == _label(message_id))
            ).one_or_none()
            return _message_view(*pair) if pair is not None else None

    def list_messages(
        self,
        principal: ConversationPrincipal | None,
        *,
        host_id: str,
        conversation_id: str,
        workspace_id: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Read a bounded message page for one scoped conversation."""
        scope = _Scope.from_principal(principal)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("Conversation page limit must be between 1 and 200")
        if type(offset) is not int or not 0 <= offset <= 2**63 - 1:
            raise ValueError("Invalid conversation page offset")
        query = (
            _with_job()
            .where(
                *scope.conditions(),
                ConversationMessage.host_id == _label(host_id),
                ConversationMessage.conversation_id == _label(conversation_id),
                ConversationMessage.workspace_id == _label(workspace_id, optional=True),
            )
            .order_by(ConversationMessage.received_at, ConversationMessage.id)
            .limit(limit)
            .offset(offset)
        )
        with self._session() as session:
            return {"messages": [_message_view(*pair) for pair in session.exec(query)]}

    def delete(self, principal: ConversationPrincipal | None, message_id: str) -> dict | None:
        """Clear the message body and cancel its job within the same transaction."""
        scope = _Scope.from_principal(principal)
        ident = _label(message_id)
        where = (*scope.conditions(), ConversationMessage.id == ident)
        with self._session() as session:
            deleted_id = session.execute(
                update(ConversationMessage)
                .where(*where)
                .values(status="deleted", content="", content_hash=None)
                .returning(ConversationMessage.id)
            ).scalar_one_or_none()
            if deleted_id is None:
                return None
            session.execute(
                update(ConversationJob)
                .where(ConversationJob.message_id == deleted_id)
                .values(status="cancelled", token=None, lease_until=None, reason_code=None)
            )
            pair = session.exec(_with_job().where(*where)).one()
            view = _message_view(*pair)
            session.commit()
            return view

    def claim(
        self,
        principal: ConversationPrincipal | None,
        *,
        lease_seconds: int = 60,
        now: datetime | None = None,
    ) -> dict | None:
        """Atomically claim the oldest available job or recover an expired lease."""
        scope = _Scope.from_principal(principal)
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError("Conversation lease must be between 1 and 3600 seconds")
        token = uuid4().hex
        # DELETE journal mode can otherwise block COMMIT on a reader and age the new lease.
        with self._session(write_lock="EXCLUSIVE") as session:
            current = _now(now).timestamp()
            lease_until = current + lease_seconds
            still_stored = ConversationJob.message_id.in_(scope.active_message_ids())
            expired = and_(
                ConversationJob.status == "processing", ConversationJob.lease_until <= current
            )
            eligible = (
                still_stored,
                ConversationJob.attempts < _MAX_ATTEMPTS,
                or_(
                    and_(
                        ConversationJob.status == "pending", ConversationJob.available_at <= current
                    ),
                    expired,
                ),
            )
            first = (
                select(ConversationJob.id)
                .where(*eligible)
                .order_by(ConversationJob.created_at, ConversationJob.id)
                .limit(1)
                .scalar_subquery()
            )
            session.execute(
                update(ConversationJob)
                .where(still_stored, expired, ConversationJob.attempts >= _MAX_ATTEMPTS)
                .values(status="held", token=None, lease_until=None, reason_code="attempt_limit")
            )
            claimed = session.execute(
                update(ConversationJob)
                .where(ConversationJob.id == first, *eligible)
                .values(
                    status="processing",
                    attempts=ConversationJob.attempts + 1,
                    token=token,
                    lease_until=lease_until,
                    reason_code=None,
                )
                .returning(ConversationJob.id, ConversationJob.message_id, ConversationJob.attempts)
            ).one_or_none()
            session.commit()
            if claimed is None:
                return None
            job_id, message_id, attempts = claimed
            return {
                "job_id": job_id,
                "message_id": message_id,
                "token": token,
                "attempts": attempts,
                "lease_until": lease_until,
            }

    def load_claim(
        self,
        principal: ConversationPrincipal | None,
        job_id: str,
        token: str,
        *,
        now: datetime | None = None,
    ) -> dict | None:
        """Load the source only while its current lease and scope remain valid."""
        scope = _Scope.from_principal(principal)
        ident, claim_token = _label(job_id), _label(token)
        conditions = _claim_conditions(scope, ident, claim_token, _now(now).timestamp())
        with self._session() as session:
            pair = session.exec(_with_job().where(*conditions)).one_or_none()
            if pair is None:
                return None
            message, job = pair
            # A read can wait on an exclusive SQLite lock too.
            if job.lease_until <= _now(now).timestamp():
                return None
            return _message_view(message, job)

    def finish(
        self,
        principal: ConversationPrincipal | None,
        job_id: str,
        token: str,
        *,
        outcome: str,
        now: datetime | None = None,
    ) -> bool:
        """Record a valid lease's outcome and release it, with bounded retry."""
        scope = _Scope.from_principal(principal)
        ident, claim_token = _label(job_id), _label(token)
        if outcome not in ("completed", "retry", "held"):
            raise ValueError("Invalid conversation job outcome")
        with self._session(write_lock="IMMEDIATE") as session:
            current = _now(now).timestamp()
            conditions = _claim_conditions(scope, ident, claim_token, current)
            values = {
                "status": outcome,
                "available_at": 0.0,
                "token": None,
                "lease_until": None,
                "reason_code": None,
            }
            if outcome == "retry":
                exhausted = ConversationJob.attempts >= _MAX_ATTEMPTS
                values.update(
                    status=case((exhausted, "held"), else_="pending"),
                    available_at=case(
                        (exhausted, 0.0), else_=current + 60 * ConversationJob.attempts
                    ),
                    reason_code=case((exhausted, "attempt_limit"), else_="retry"),
                )
            elif outcome == "held":
                values["reason_code"] = "held"
            finished = session.execute(
                update(ConversationJob)
                .where(*conditions)
                .values(**values)
                .returning(ConversationJob.id)
            ).scalar_one_or_none()
            session.commit()
            return finished is not None
