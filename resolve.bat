@echo off
setlocal enabledelayedexpansion
set "BASE_DIR=%~dp0"
if "%BASE_DIR%"=="" set "BASE_DIR=.\"

if defined PYTHON (
  set "PYTHON_CMD=%PYTHON%"
) else (
  set "PYTHON_CMD=python"
)

"%PYTHON_CMD%" -c "import sys" >nul 2>nul
if errorlevel 1 (
  echo Python interpreter '%PYTHON_CMD%' not found or failed to start.>&2
  exit /b 1
)

set "SCRIPT=%BASE_DIR%scripts\resolve_sigils.py"
"%PYTHON_CMD%" "%SCRIPT%" %*
exit /b %errorlevel%
