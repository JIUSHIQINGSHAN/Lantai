"""Hermes-safe MCP launcher.

Problem: Hermes sets PYTHONPATH to its own venv site-packages, which pollutes subprocess imports.
The Hermes venv has a broken pydantic install that shadows the project venv's working pydantic.
Solution: Clear PYTHONPATH entirely and locate the project from this file's own position.

Ticket `.scratch/v0.68-next-window-2026-11/05-mcp-entry-code-broken.md`:
- Entry is the single contract `lantai.cli.mcp:main` (same as the pyproject console
  script `lantai-mcp`) — the old flat module under `scripts/` no longer exists.
- Repo root is derived from `__file__`; the old hardcoded machine path and the dead
  old-venv name are gone (regression-locked in `tests/test_mcp_entry_points.py`).
- If the running interpreter is not the project venv, we let the import fail with an
  explainable ModuleNotFoundError instead of silently grafting a foreign venv's
  site-packages underneath — that grafting is exactly the pollution this launcher exists to fight.
"""

import os
import sys
from pathlib import Path

# --- Clear Hermes venv pollution ---
os.environ.pop("PYTHONPATH", None)
os.environ.pop("PYTHONHOME", None)

# Remove Hermes paths from sys.path (inherited via PYTHONPATH)
hermes_paths = [p for p in sys.path if "hermes" in p.lower()]
for p in hermes_paths:
    sys.path.remove(p)

# --- Set up project paths (repo root from this file, not cwd/machine layout) ---
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# If the in-repo venv exists, align PATH/VIRTUAL_ENV for any subprocess the server
# may spawn; absence is fine — the caller's interpreter is expected to be the
# project venv (launch_mcp.bat / the installed console script both guarantee that).
VENV_DIR = ROOT / ".venv"
if VENV_DIR.exists():
    venv_bin = VENV_DIR / ("Scripts" if (VENV_DIR / "Scripts").exists() else "bin")
    if str(venv_bin) not in os.environ.get("PATH", ""):
        os.environ["PATH"] = f"{venv_bin}{os.pathsep}{os.environ.get('PATH', '')}"
    os.environ["VIRTUAL_ENV"] = str(VENV_DIR)

# --- Load .env file ---
try:
    from dotenv import load_dotenv

    env_path = ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        print(f"[launcher] Loaded .env from {env_path}", file=sys.stderr)
except ImportError:
    pass

# Now import and run the MCP server (single entry contract: lantai.cli.mcp:main)
from lantai.cli.mcp import main

if __name__ == "__main__":
    main()
