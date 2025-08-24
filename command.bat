@echo off
set VENV=.venv
if not exist %VENV%\Scripts\python.exe (
    echo Virtual environment not found. Run install.sh first.
    exit /b 1
)
if "%~1"=="" (
    echo Usage: %0 ^<command^> [args...]
    exit /b 1
)
set COMMAND=%1
set COMMAND=%COMMAND:-=_%
shift
%VENV%\Scripts\python.exe manage.py %COMMAND% %*
