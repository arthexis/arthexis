@echo off
set VENV=.venv
if not exist %VENV%\Scripts\python.exe (
    echo Virtual environment not found. Run install.sh first.
    exit /b 1
)

set PORT=8000
set RELOAD=
:parse
if "%1"=="" goto run
if "%1"=="--port" (
    set PORT=%2
    shift
    shift
    goto parse
)
if "%1"=="--reload" (
    set RELOAD=1
    shift
    goto parse
)
echo Usage: %0 [--port PORT] [--reload]
exit /b 1

:run
if not defined RELOAD set NORELOAD=--noreload
%VENV%\Scripts\python.exe manage.py collectstatic --noinput
%VENV%\Scripts\python.exe manage.py runserver 0.0.0.0:%PORT% %NORELOAD%
