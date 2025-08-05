@echo off
set VENV=.venv
if not exist %VENV%\Scripts\python.exe (
    py -m venv %VENV%
)
if not exist %VENV%\Scripts\python.exe (
    echo Virtual environment not found
    exit /b 0
)
if exist requirements.txt (
    %VENV%\Scripts\python.exe -m pip install -r requirements.txt
)
set "DB_FILE=%VENV%\..\db.sqlite3"
if defined DB_PATH set "DB_FILE=%DB_PATH%"
if exist "%DB_FILE%" del "%DB_FILE%"
%VENV%\Scripts\python.exe dev_maintenance.py
