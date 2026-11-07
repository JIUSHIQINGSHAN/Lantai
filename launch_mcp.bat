@echo off
REM Isolated launcher for 兰台记忆 MCP server
REM 入口：lantai.cli.mcp（票 .scratch/v068-docs-audit/01）；仓库根与 venv
REM 从本脚本所在目录解析，不写死盘符。
setlocal

REM Clear Hermes venv pollution
set PYTHONPATH=
set PYTHONHOME=

REM Resolve repo root from this script's location
set ROOT=%~dp0
if "%ROOT:~-1%"=="\" set ROOT=%ROOT:~0,-1%

REM Pick project venv (.venv first, legacy .venv-audit fallback)
set VENV=%ROOT%\.venv
if not exist "%VENV%\Scripts\python.exe" set VENV=%ROOT%\.venv-audit

set PATH=%VENV%\Scripts;%PATH%
set VIRTUAL_ENV=%VENV%

REM Run MCP server (repo root on sys.path via -m + cwd)
cd /d "%ROOT%"
"%VENV%\Scripts\python.exe" -m lantai.cli.mcp
