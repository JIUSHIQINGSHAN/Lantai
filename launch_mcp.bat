@echo off
REM Isolated launcher for Remembrance MCP server
setlocal

REM Clear Hermes venv pollution
set PYTHONPATH=
set PYTHONHOME=

REM Activate project venv
set PATH=C:\Users\Asus\Desktop\记忆\.venv-audit\Scripts;%PATH%
set VIRTUAL_ENV=C:\Users\Asus\Desktop\记忆\.venv-audit

REM Run MCP server
"C:\Users\Asus\Desktop\记忆\.venv-audit\Scripts\python.exe" -c "
import sys
sys.path.insert(0, r'C:\Users\Asus\Desktop\记忆')
sys.path.insert(0, r'C:\Users\Asus\Desktop\记忆\.venv-audit\Lib\site-packages')
from mcp_server import main
main()
"
