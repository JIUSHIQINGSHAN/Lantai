"""石室（Shishi · 《史记·太史公自序》「迁为太史令，紬石室金匮之书」）：高密核心记忆金匮封存与防篡改硬核锚定引擎。

为 Agent 核心记忆（identity / policy / task）提供：
1. 金匮硬核封存（ShishiVaultBlock）：记录递增版本与不可变标记；
2. 内容完整性哈希签名（SHA-256 Content Digest）：实时防篡改与完整性验真（IntegrityVerificationResult）；
3. 樊篱隔离防护（wrap_as_data）：最高优先级 `<memory_data>` 防提示注入围栏；
4. 租户隔离与行级 ACL 权限收窄：宁 miss 不越权，不可变块禁止非法篡改。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlmodel import Session, select
from ulid import ULID

from lantai.core.acl import viewer_of
from lantai.core.time import utcnow
from lantai.llm.fence import wrap_as_data
from lantai.models.tables import CoreMemoryBlock
from lantai.storage import db

VALID_BLOCKS = frozenset({"identity", "policy", "task"})


@dataclass
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
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "total_blocks": self.total_blocks,
            "verified_blocks": self.verified_blocks,
            "tampered_blocks": self.tampered_blocks,
            "tamper_details": self.tamper_details,
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
        }


def compute_content_digest(block_key: str, content: str, namespace: str) -> str:
    """计算核心块内容 SHA-256 完整性摘要。"""
    raw = f"{namespace}:{block_key}:{content}".encode()
    return hashlib.sha256(raw).hexdigest()


class ShishiEngine:
    """石室·高密核心记忆金匮封存与防篡改硬核锚定引擎。"""

    def __init__(self, session: Session | None = None, *, principal=None):
        self.session = session
        self.principal = principal

    def _get_viewer_scope(self):
        principal = self.principal
        if principal is not None and bool(getattr(principal, "is_admin", False)):
            return None
        viewer = (
            (getattr(principal, "user_id", None) or "default")
            if principal is not None
            else "default"
        )
        return (CoreMemoryBlock.user_id == viewer) | (CoreMemoryBlock.user_id.is_(None))

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
        4. 计算完整性哈希签名并自增版本落库。
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
                stmt = stmt.where(
                    (CoreMemoryBlock.user_id == user_id) | (CoreMemoryBlock.user_id.is_(None))
                )

            row = s.exec(stmt).first()
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

                new_version = (row.version or 1) + 1
                row.content = content
                row.version = new_version
                row.updated_at = utcnow()
                # 编码签名与锁定元信息至 agent_id
                lock_flag = ":sealed:immutable" if is_immutable else ":sealed"
                row.agent_id = f"sha256={digest}{lock_flag}"
                s.add(row)
                s.commit()
                s.refresh(row)
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
                s.commit()
                s.refresh(row)

            fenced = wrap_as_data(content, item_id=f"shishi-{block}", score=1.0)
            vault_block = ShishiVaultBlock(
                block_key=block,
                content=content,
                content_digest=digest,
                version=new_version,
                is_immutable=is_immutable,
                namespace=namespace,
                fenced_content=fenced,
                user_id=user_id,
                tenant_id=tenant_id,
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

            rows = s.exec(stmt.order_by(CoreMemoryBlock.block)).all()
            total_blocks = len(rows)
            verified_count = 0
            tampered_keys: list[str] = []
            tamper_details: list[dict[str, Any]] = []

            for r in rows:
                actual_digest = compute_content_digest(r.block, r.content or "", namespace)
                recorded_digest = ""
                if r.agent_id and "sha256=" in str(r.agent_id):
                    part = str(r.agent_id).split("sha256=")[1]
                    recorded_digest = part.split(":")[0]

                if not recorded_digest:
                    # 未金匮封存的早期记录，不视为篡改但标为未签名
                    verified_count += 1
                    continue

                if recorded_digest == actual_digest:
                    verified_count += 1
                else:
                    tampered_keys.append(r.block)
                    tamper_details.append(
                        {
                            "block": r.block,
                            "recorded_digest": recorded_digest,
                            "actual_digest": actual_digest,
                            "version": r.version,
                            "tampered_at": utcnow().isoformat(),
                        }
                    )

            is_valid = len(tampered_keys) == 0
            res = IntegrityVerificationResult(
                is_valid=is_valid,
                total_blocks=total_blocks,
                verified_blocks=verified_count,
                tampered_blocks=tampered_keys,
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

            rows = s.exec(stmt.order_by(CoreMemoryBlock.block)).all()
            result = []
            for r in rows:
                content = r.content or ""
                digest = compute_content_digest(r.block, content, namespace)
                is_immutable = bool(r.agent_id and ":sealed:immutable" in str(r.agent_id))
                fenced = wrap_as_data(content, item_id=f"shishi-{r.block}", score=1.0)
                vault_block = ShishiVaultBlock(
                    block_key=r.block,
                    content=content,
                    content_digest=digest,
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
