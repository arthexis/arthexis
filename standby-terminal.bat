@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PYTHON=%SCRIPT_DIR%.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"
"%PYTHON%" "%SCRIPT_DIR%scripts\standby_terminal.py" %*
set "EXITCODE=%ERRORLEVEL%"
endlocal & exit /b %EXITCODE%
