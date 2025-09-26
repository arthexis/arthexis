@echo off
set VENV=.venv
if not exist %VENV%\Scripts\python.exe (
    echo Virtual environment not found. Run install.sh or install.bat first.
    exit /b 1
)
if "%~1"=="" (
    echo Available Django management commands:
    %VENV%\Scripts\python.exe manage.py help --commands
    echo.
    echo Usage: %~nx0 ^<command^> [args...]
    exit /b 0
)
set COMMAND=%1
set COMMAND=%COMMAND:-=_%
shift
%VENV%\Scripts\python.exe manage.py %COMMAND% %*
