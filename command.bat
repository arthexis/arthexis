@echo off
setlocal
set "VENV=.venv"
if not exist "%VENV%\Scripts\python.exe" (
    echo Virtual environment not found. Run install.sh or install.bat first.
    exit /b 1
)

REM Supported interface:
REM   Usage: command.bat <operational-command> [args...]
REM   Usage: command.bat list
REM For non-operational/admin commands, use manage.py directly.
"%VENV%\Scripts\python.exe" -m utils.command_api %*
set "EXITCODE=%ERRORLEVEL%"
endlocal & exit /b %EXITCODE%
