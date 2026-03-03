@echo off
setlocal
set "VENV=.venv"
if not exist "%VENV%\Scripts\python.exe" (
    echo Virtual environment not found. Run install.sh or install.bat first.
    exit /b 1
)

REM Windows wrapper intentionally invokes the API directly and does not require
REM a detected running instance, unlike command.sh on POSIX shells.
REM Canonical interface:
REM   Usage: arthexis cmd list [--deprecated] [--celery|--no-celery]
REM   Usage: arthexis cmd run [--deprecated] [--celery|--no-celery] <django-command> [args...]

"%VENV%\Scripts\python.exe" -m utils.command_api %*
set "EXITCODE=%ERRORLEVEL%"
endlocal & exit /b %EXITCODE%
