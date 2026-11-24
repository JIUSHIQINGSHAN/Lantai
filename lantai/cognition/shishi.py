"""石室（Shishi · 《史记·太史公自序》「迁为太史令，紬石室金匮之书」）：高密核心记忆金匮封存与防篡改硬核锚定引擎。

为 Agent 核心记忆（identity / policy / task）提供：
1. 金匮硬核封存（ShishiVaultBlock）：记录递增版本与不可变标记；
2. 内容完整性摘要（SHA-256 Content Digest）：篡改感知与完整性验真（IntegrityVerificationResult）；
3. 樊篱隔离防护（wrap_as_data）：最高优先级 `<memory_data>` 防提示注入围栏；
4. 租户隔离与行级 ACL 权限收窄：宁 miss 不越权，不可变块禁止非法篡改。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select
from ulid import ULID

from lantai.core.acl import viewer_of
from lantai.core.time import iso_utc, utcnow
from lantai.llm.fence import wrap_as_data
from lantai.models.tables import CoreMemoryBlock
from lantai.storage import db
from lantai.storage.cas import atomic_update

VALID_BLOCKS = frozenset({"identity", "policy", "task"})


class ShishiConflictError(RuntimeError):
    """核心记忆的封存状态在读取后改变，调用方须重读后重试。"""


@dataclass(frozen=True)
class ShishiVaultBlock:
    """金匮核心记忆封存块契约。"""

    block_key: str  # identity | policy | task
    content: str
    content_digest: str
    version: int
    is_immutable: bool
    namespace: str
    fenced_content: str
    user_id: str | None = None
    tenant_id: str | None = None
    updated_at: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_key": self.block_key,
            "content": self.content,
            "content_digest": self.content_digest,
            "version": self.version,
            "is_immutable": self.is_immutable,
            "namespace": self.namespace,
            "fenced_content": self.fenced_content,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "updated_at": iso_utc(self.updated_at) or None,
        }


@dataclass
class IntegrityVerificationResult:
    """石室核心记忆完整性验真报告契约。"""

    is_valid: bool
    total_blocks: int
    verified_blocks: int
    tampered_blocks: list[str]
    tamper_details: list[dict[str, Any]]
    verified_at: datetime = field(default_factory=utcnow)
    unsealed_blocks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "total_blocks": self.total_blocks,
            "verified_blocks": self.verified_blocks,
            "tampered_blocks": self.tampered_blocks,
            "unsealed_blocks": self.unsealed_blocks,
            "tamper_details": self.tamper_details,
            "verified_at": iso_utc(self.verified_at) or None,
        }


def compute_content_digest(block_key: str, content: str, namespace: str) -> str:
    """计算核心块内容 SHA-256 完整性摘要。"""
    raw = f"{namespace}:{block_key}:{content}".encode()
    return hashlib.sha256(raw).hexdigest()


def _parse_seal_metadata(agent_id: str | None) -> tuple[str | None, bool, bool, bool]:
    """返回摘要、不可变标记、是否有封存标记及元数据格式是否损坏。"""
    value = str(agent_id or "")
    has_seal_marker = "sha256=" in value or ":sealed" in value
    if not has_seal_marker:
        return None, False, False, False

    immutable_suffix = ":sealed:immutable"
    mutable_suffix = ":sealed"
    if value.endswith(immutable_suffix):
        suffix = immutable_suffix
        is_immutable = True
    elif value.endswith(mutable_suffix):
        suffix = mutable_suffix
        is_immutable = False
    else:
        return None, False, True, True

    if not value.startswith("sha256="):
        return None, is_immutable, True, True

    digest = value.removeprefix("sha256=").removesuffix(suffix)
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        return None, is_immutable, True, True
    return digest, is_immutable, True, False


def validate_existing_seal(row: CoreMemoryBlock) -> tuple[str | None, bool]:
    """验证已有封存；未封存返回空摘要，损坏封存拒绝续写。"""
    digest, immutable, sealed, malformed = _parse_seal_metadata(row.agent_id)
    if malformed:
        raise PermissionError(
            f"CoreMemoryBlock '{row.block}' in namespace '{row.namespace}' "
            "has invalid Shishi seal metadata"
        )
    if sealed and digest != compute_content_digest(row.block, row.content or "", row.namespace):
        raise PermissionError(
            f"CoreMemoryBlock '{row.block}' in namespace '{row.namespace}' "
            "failed Shishi integrity verification"
        )
    return digest, immutable


def _update_unlocked_block(
    session: Session,
    row: CoreMemoryBlock,
    *,
    values: dict[str, Any],
    block: str,
    namespace: str,
    is_admin: bool,
) -> None:
    guard = (
        CoreMemoryBlock.block == block,
        CoreMemoryBlock.namespace == namespace,
        CoreMemoryBlock.user_id == row.user_id,
        CoreMemoryBlock.tenant_id == row.tenant_id,
    )
    if _parse_seal_metadata(row.agent_id)[2]:
        # 已封存行须保留读时证据；普通未封存行继续遵循末写胜与库端版本自增。
        guard += (
            CoreMemoryBlock.agent_id == row.agent_id,
            CoreMemoryBlock.content == row.content,
        )
    if not is_admin:
        guard += (
            or_(
                CoreMemoryBlock.agent_id.is_(None),
                CoreMemoryBlock.agent_id.not_like("%:sealed:immutable%"),
            ),
        )

    if atomic_update(session, CoreMemoryBlock, row.id, values, *guard):
        return

    session.rollback()
    current = session.get(CoreMemoryBlock, row.id)
    if (
        not is_admin
        and current is not None
        and current.agent_id
        and ":sealed:immutable" in str(current.agent_id)
    ):
        raise PermissionError(
            f"CoreMemoryBlock '{block}' in namespace '{namespace}' "
            "is sealed in Shishi vault and immutable"
        )
    raise ShishiConflictError(
        f"CoreMemoryBlock '{block}' in namespace '{namespace}' changed during seal; retry"
    )


class ShishiEngine:
    """石室·高密核心记忆金匮封存与防篡改硬核锚定引擎。"""

    def __init__(self, session: Session | None = None, *, principal=None):
        self.session = session
        self.principal = principal

    def _get_viewer_scope(self):
        principal = self.principal
        if principal is not None and bool(getattr(principal, "is_admin", False)):
            return None
        scope = (CoreMemoryBlock.user_id == viewer_of(principal)) | (
            CoreMemoryBlock.user_id.is_(None)
        )
        tenant_id = getattr(principal, "tenant_id", None)
        if tenant_id:
            scope &= (CoreMemoryBlock.tenant_id == tenant_id) | (
                CoreMemoryBlock.tenant_id.is_(None)
            )
        return scope

    def seal_core_memory(
        self,
        block: str,
        content: str,
        *,
        is_immutable: bool = True,
        namespace: str = "default",
    ) -> dict[str, Any]:
        """封存或更新核心记忆块。

        1. 校验合规块名；
        2. 校验 prompt injection 注入防御；
        3. 检查行级 ACL 与不可变加锁，已锁定时非管理员直接报错拒收；
        4. 计算完整性摘要并自增版本落库。
        """
        if block not in VALID_BLOCKS:
            raise ValueError(f"Invalid core block '{block}', must be one of {sorted(VALID_BLOCKS)}")

        from lantai.security.injection_guard import assert_no_prompt_injection

        assert_no_prompt_injection(content)

        principal = self.principal
        is_admin = bool(getattr(principal, "is_admin", False)) if principal is not None else False
        user_id = (
            getattr(principal, "user_id", None) or "default" if principal is not None else "default"
        )
        tenant_id = getattr(principal, "tenant_id", None) if principal is not None else None

        def _seal(s: Session) -> dict[str, Any]:
            stmt = select(CoreMemoryBlock).where(
                CoreMemoryBlock.block == block,
                CoreMemoryBlock.namespace == namespace,
            )
            if not is_admin:
                # 写面只认自己的行(票 68 位点 5):读面「本人或 ownerless」的可见
                # 范围若用于写查找,任意用户可改写 ownerless 行——宁 miss 不脏写,
                # ownerless 行不进写范围,封存落自己名下新行。
                stmt = stmt.where(CoreMemoryBlock.user_id == user_id)
                if tenant_id:
                    # 写面租户判据(票 68 位点 5 的 tenant 缺席半边,审查补):
                    # 同 user_id 异租户的行不进写范围(宁 miss 不脏写,落本租户
                    # 新行);本租户未知(ownerless-tenant 行)不拦,与 _check_access
                    # 「双方都有 tenant 才比对」的口径一致。
                    stmt = stmt.where(
                        (CoreMemoryBlock.tenant_id.is_(None))
                        | (CoreMemoryBlock.tenant_id == tenant_id)
                    )
            else:
                # admin 定靶确定性(票 68 位点 5):同 (block, namespace) 多用户行
                # 并存时裸 .first() 命中哪行全凭存储序(SQLite 实况 rowid),按
                # (user_id, tenant_id) 升序确定性取最小属主行。
                stmt = stmt.order_by(CoreMemoryBlock.user_id, CoreMemoryBlock.tenant_id)

            row = s.exec(stmt.execution_options(populate_existing=True)).first()
            digest = compute_content_digest(block, content, namespace)

            if row is not None:
                # 检查不可变锁定（存储在 agent_id 元数据扩展或者通过版本判断）
                # 我们通过 agent_id 携带 ":sealed:immutable" 标识防篡改
                is_currently_locked = bool(
                    row.agent_id and ":sealed:immutable" in str(row.agent_id)
                )
                if is_currently_locked and not is_admin:
                    raise PermissionError(
                        f"CoreMemoryBlock '{block}' in namespace '{namespace}' is sealed in Shishi vault and immutable"
                    )
                if not is_admin:
                    validate_existing_seal(row)

                lock_flag = ":sealed:immutable" if is_immutable else ":sealed"
                # 票 23：版本号库端自增——两路并发封存各 +1 都落账（红测 F5b）；
                # content/签名/锁定元信息同语句原子（末写者胜）。
                _update_unlocked_block(
                    s,
                    row,
                    values={
                        "content": content,
                        "version": CoreMemoryBlock.version + 1,
                        "updated_at": utcnow(),
                        "agent_id": f"sha256={digest}{lock_flag}",
                    },
                    block=block,
                    namespace=namespace,
                    is_admin=is_admin,
                )
                s.commit()
                s.refresh(row)
                new_version = row.version
                target_id = row.id
            else:
                new_version = 1
                target_id = f"cmb_{ULID()}"
                lock_flag = ":sealed:immutable" if is_immutable else ":sealed"
                encoded_agent = f"sha256={digest}{lock_flag}"
                row = CoreMemoryBlock(
                    id=target_id,
                    block=block,
                    content=content,
                    namespace=namespace,
                    version=new_version,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    agent_id=encoded_agent,
                    updated_at=utcnow(),
                )
                s.add(row)
                try:
                    s.commit()
                except IntegrityError:
                    # 票 23：并发首写撞 (block, namespace, user_id) 唯一约束——
                    # 败方重读既有行按更新路收敛（末写者胜），不再双插双行；
                    # 重读行可能是他人加锁的 sealed 行，不可变检查重验。
                    s.rollback()
                    row = s.exec(stmt).first()
                    if row is None:
                        raise
                    if row.agent_id and ":sealed:immutable" in str(row.agent_id) and not is_admin:
                        raise PermissionError(
                            f"CoreMemoryBlock '{block}' in namespace '{namespace}' is sealed in Shishi vault and immutable"
                        ) from None
                    if not is_admin:
                        validate_existing_seal(row)
                    _update_unlocked_block(
                        s,
                        row,
                        values={
                            "content": content,
                            "version": CoreMemoryBlock.version + 1,
                            "updated_at": utcnow(),
                            "agent_id": encoded_agent,
                        },
                        block=block,
                        namespace=namespace,
                        is_admin=is_admin,
                    )
                    s.commit()
                    s.refresh(row)
                    new_version = row.version
                    target_id = row.id

            fenced = wrap_as_data(content, item_id=f"shishi-{block}", score=1.0)
            vault_block = ShishiVaultBlock(
                block_key=block,
                content=content,
                content_digest=digest,
                version=new_version,
                is_immutable=is_immutable,
                namespace=namespace,
                fenced_content=fenced,
                user_id=row.user_id,
                tenant_id=row.tenant_id,
                updated_at=row.updated_at,
            )
            return vault_block.to_dict()

        if self.session is not None:
            return _seal(self.session)
        with db.get_session() as s:
            return _seal(s)

    def verify_integrity(self, namespace: str = "default") -> dict[str, Any]:
        """对指定命名空间下的核心记忆进行完整性哈希验真。"""

        def _verify(s: Session) -> dict[str, Any]:
            stmt = select(CoreMemoryBlock).where(CoreMemoryBlock.namespace == namespace)
            scope = self._get_viewer_scope()
            if scope is not None:
                stmt = stmt.where(scope)

            rows = s.exec(
                stmt.order_by(CoreMemoryBlock.block).execution_options(populate_existing=True)
            ).all()
            total_blocks = len(rows)
            verified_count = 0
            tampered_keys: list[str] = []
            unsealed_keys: list[str] = []
            tamper_details: list[dict[str, Any]] = []

            for r in rows:
                actual_digest = compute_content_digest(r.block, r.content or "", namespace)
                recorded_digest, _, is_sealed, malformed = _parse_seal_metadata(r.agent_id)

                if not is_sealed:
                    unsealed_keys.append(r.block)
                    continue

                if not malformed and recorded_digest == actual_digest:
                    verified_count += 1
                else:
                    tampered_keys.append(r.block)
                    tamper_details.append(
                        {
                            "block": r.block,
                            "recorded_digest": recorded_digest,
                            "actual_digest": actual_digest,
                            "version": r.version,
                            "reason": "invalid_signature" if malformed else "digest_mismatch",
                            "tampered_at": iso_utc(utcnow()),
                        }
                    )

            is_valid = not tampered_keys and not unsealed_keys
            res = IntegrityVerificationResult(
                is_valid=is_valid,
                total_blocks=total_blocks,
                verified_blocks=verified_count,
                tampered_blocks=tampered_keys,
                unsealed_blocks=unsealed_keys,
                tamper_details=tamper_details,
            )
            return res.to_dict()

        if self.session is not None:
            return _verify(self.session)
        with db.get_session() as s:
            return _verify(s)

    def get_sealed_blocks(self, namespace: str = "default") -> list[dict[str, Any]]:
        """读取已封存的核心记忆块，并严格樊篱包裹输出。"""

        def _fetch(s: Session) -> list[dict[str, Any]]:
            stmt = select(CoreMemoryBlock).where(CoreMemoryBlock.namespace == namespace)
            scope = self._get_viewer_scope()
            if scope is not None:
                stmt = stmt.where(scope)

            rows = s.exec(
                stmt.order_by(CoreMemoryBlock.block).execution_options(populate_existing=True)
            ).all()
            result = []
            for r in rows:
                content = r.content or ""
                digest = compute_content_digest(r.block, content, namespace)
                recorded_digest, is_immutable, is_sealed, malformed = _parse_seal_metadata(
                    r.agent_id
                )
                if not is_sealed or malformed or recorded_digest != digest:
                    continue
                fenced = wrap_as_data(content, item_id=f"shishi-{r.block}", score=1.0)
                vault_block = ShishiVaultBlock(
                    block_key=r.block,
                    content=content,
                    content_digest=recorded_digest,
                    version=r.version or 1,
                    is_immutable=is_immutable,
                    namespace=namespace,
                    fenced_content=fenced,
                    user_id=r.user_id,
                    tenant_id=r.tenant_id,
                    updated_at=r.updated_at,
                )
                result.append(vault_block.to_dict())
            return result

        if self.session is not None:
            return _fetch(self.session)
        with db.get_session() as s:
            return _fetch(s)
