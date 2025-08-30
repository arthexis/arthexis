@echo off
set VENV=.venv
set LATEST=0
set CLEAN=0
:parse
if "%1"=="" goto after_parse
if "%1"=="--latest" (
    set LATEST=1
    shift
    goto parse
)
if "%1"=="--clean" (
    set CLEAN=1
    shift
    goto parse
)
goto after_parse
:after_parse
if not exist %VENV%\Scripts\python.exe (
    echo Virtual environment not found. Run install.sh first.
    exit /b 1
)

if %CLEAN%==1 (
    del /f /q "%~dp0db.sqlite3" 2>nul
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
if %LATEST%==1 (
    %VENV%\Scripts\python.exe env-refresh.py --latest database
) else (
    %VENV%\Scripts\python.exe env-refresh.py database
)
