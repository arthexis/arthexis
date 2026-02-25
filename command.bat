@echo off
setlocal
set "VENV=.venv"
if not exist "%VENV%\Scripts\python.exe" (
    echo Virtual environment not found. Run install.sh or install.bat first.
    exit /b 1
)

REM Canonical interface:
REM   Usage: arthexis cmd list [--deprecated] [--celery|--no-celery]
REM   Usage: arthexis cmd run [--deprecated] [--celery|--no-celery] <django-command> [args...]

"%VENV%\Scripts\python.exe" -m utils.command_api %*
set "EXITCODE=%ERRORLEVEL%"
endlocal & exit /b %EXITCODE%
