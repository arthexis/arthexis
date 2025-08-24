@echo off
set VENV=.venv
if not exist %VENV%\Scripts\python.exe (
    echo Virtual environment not found. Run install.sh first.
    exit /b 1
)
set PORT=%1
if "%PORT%"=="" set PORT=8000
%VENV%\Scripts\python.exe manage.py runserver 0.0.0.0:%PORT%
