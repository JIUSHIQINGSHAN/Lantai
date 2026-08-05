"""Hermes-safe MCP launcher.

Problem: Hermes sets PYTHONPATH to its own venv site-packages, which pollutes subprocess imports.
The Hermes venv has a broken pydantic install that shadows the project venv's working pydantic.
Solution: Clear PYTHONPATH entirely and set up the project venv manually.
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

# --- Set up project venv ---
ROOT = Path(r"C:\Users\Asus\Desktop\记忆")
SCRIPTS_DIR = str(ROOT / "scripts")
VENV_SITE = str(ROOT / ".venv-audit" / "Lib" / "site-packages")
VENV_SCRIPTS = str(ROOT / ".venv-audit" / "Scripts")

# Prepend project paths (scripts dir first so mcp_server can be imported)
sys.path.insert(0, SCRIPTS_DIR)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, VENV_SITE)

# Update PATH and VIRTUAL_ENV for any subprocess
os.environ["PATH"] = f"{VENV_SCRIPTS}{os.pathsep}{os.environ.get('PATH', '')}"
os.environ["VIRTUAL_ENV"] = str(ROOT / ".venv-audit")

# --- Load .env file ---
try:
    from dotenv import load_dotenv
    env_path = ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        print(f"[launcher] Loaded .env from {env_path}", file=sys.stderr)
except ImportError:
    pass

# Now import and run the MCP server
from mcp_server import main  # noqa: E402

if __name__ == "__main__":
    main()
