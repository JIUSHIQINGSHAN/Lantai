"""Ticket 83 regression sentinels for ADR-0002 script hygiene."""

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"

_MACHINE_HOME = re.compile(
    rb"(?i)\b[A-Z]:\\Users\\[^\\\r\n]+\\AppData\\Local\\remembrance-data(?:\\|\b)"
)
_FALLBACK_SECRET = re.compile(rb"(?i)lantai-local-sync-secret-\d{4}-\d{2}")
_CWD_IMPORT_TARGETS = {".", "./", "scripts", "./scripts"}


def _script_sources() -> list[tuple[Path, bytes]]:
    return [(path, path.read_bytes()) for path in sorted(SCRIPTS_ROOT.glob("*.py"))]


def _line_of(blob: bytes, offset: int) -> int:
    return blob[:offset].count(b"\n") + 1


def test_scripts_do_not_embed_machine_specific_homes_or_fallback_secrets():
    hits = []
    for path, blob in _script_sources():
        for category, pattern in (
            ("machine-home", _MACHINE_HOME),
            ("fallback-secret", _FALLBACK_SECRET),
        ):
            for match in pattern.finditer(blob):
                hits.append(
                    f"{path.relative_to(REPO_ROOT)}:{_line_of(blob, match.start())}:{category}"
                )

    assert not hits, "sensitive script literals remain (path:line:category):\n" + "\n".join(hits)


def _is_cwd_relative_target(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value in _CWD_IMPORT_TARGETS
    if isinstance(node, ast.Call):
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "getcwd"
            and isinstance(func.value, ast.Name)
            and func.value.id == "os"
        ):
            return True
    return any(_is_cwd_relative_target(child) for child in ast.iter_child_nodes(node))


def test_scripts_do_not_write_cwd_relative_import_paths():
    hits = []
    for path, blob in _script_sources():
        tree = ast.parse(blob, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"insert", "append"}:
                continue
            receiver = node.func.value
            if not (
                isinstance(receiver, ast.Attribute)
                and receiver.attr == "path"
                and isinstance(receiver.value, ast.Name)
                and receiver.value.id == "sys"
            ):
                continue
            target = (
                node.args[1]
                if node.func.attr == "insert" and len(node.args) > 1
                else (node.args[0] if node.args else None)
            )
            if target is not None and _is_cwd_relative_target(target):
                hits.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}:cwd-relative-sys-path")

    assert not hits, "CWD-relative import paths remain (path:line:category):\n" + "\n".join(hits)
