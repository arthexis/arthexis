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
    for /f "skip=1 tokens=1" %%h in ('certutil -hashfile requirements.txt MD5') do (
        if not defined REQ_HASH set REQ_HASH=%%h
    )
    if exist requirements.md5 (
        set /p STORED_HASH=<requirements.md5
    )
    if /I not "%REQ_HASH%"=="%STORED_HASH%" (
        %VENV%\Scripts\python.exe -m pip install -r requirements.txt
        echo %REQ_HASH%>requirements.md5
    )
)
%VENV%\Scripts\python.exe dev_maintenance.py database
