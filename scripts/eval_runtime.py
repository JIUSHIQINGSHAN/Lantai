"""Shared evaluation launcher configuration; no application/storage imports or I/O."""

from __future__ import annotations

import os
from pathlib import Path


def require_api_key(dotenv: dict[str, str]) -> str:
    """Use one configured key for both server and client; never invent credentials."""
    value = (os.environ.get("API_KEY") or dotenv.get("API_KEY") or "").strip()
    if not value:
        raise RuntimeError("API_KEY must be configured for evaluation")
    return value


def production_home(
    dotenv: dict[str, str] | None = None,
    *,
    repo_root: Path | None = None,
    require_explicit: bool = False,
) -> Path:
    """Resolve the protected home before substituting an evaluation LANTAI_HOME.

    Launchers may derive it from their existing data configuration. The spawned
    server requires the explicit forwarded value, because its LANTAI_HOME already
    names the evaluation directory and cannot identify the production directory.
    """
    explicit = (os.environ.get("REMEMBRANCE_PROD_HOME") or "").strip()
    if require_explicit:
        if not explicit:
            raise SystemExit("REMEMBRANCE_PROD_HOME 未设置，拒启评测服务器")
        return Path(explicit).expanduser().resolve()
    values = dotenv or {}
    value = explicit or values.get("REMEMBRANCE_PROD_HOME")
    if not value:
        value = (
            os.environ.get("LANTAI_HOME")
            or values.get("LANTAI_HOME")
            or os.environ.get("REMEMBRANCE_HOME")
            or values.get("REMEMBRANCE_HOME")
        )
    if not value:
        if repo_root is None:
            raise SystemExit("REMEMBRANCE_PROD_HOME 未设置，无法确定生产目录")
        value = str(repo_root)
    return Path(value).expanduser().resolve()


def assert_separate_home(evaluation: Path, production: Path) -> Path:
    """Reject equal/ancestor/descendant paths, without touching either directory."""
    ev = evaluation.expanduser().resolve()
    prod = production.expanduser().resolve()
    if ev == prod or ev.is_relative_to(prod) or prod.is_relative_to(ev):
        raise SystemExit(f"双闸失败：评测目录 {ev} 与生产路径 {prod} 重叠，拒跑")
    return ev
