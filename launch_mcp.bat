@echo off
REM Isolated launcher for the Lantai MCP server.
REM Ticket v0.68/05: single entry contract = lantai.cli.mcp:main (python -m lantai.cli.mcp),
REM located relative to this script (%~dp0) -- no machine-specific absolute paths.
setlocal

REM Clear Hermes venv pollution
set "PYTHONPATH="
set "PYTHONHOME="

REM Repo root = directory of this bat; run from there so -m resolves the package
cd /d "%~dp0"

REM Prefer the in-repo venv; fall back to python on PATH (installed console script)
set "PYTHON_EXE=python"
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PATH=%~dp0.venv\Scripts;%PATH%"
    set "VIRTUAL_ENV=%~dp0.venv"
    set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
)

REM Run the MCP server (stdio JSON-RPC loop; reads stdin, exits on EOF)
"%PYTHON_EXE%" -m lantai.cli.mcp
