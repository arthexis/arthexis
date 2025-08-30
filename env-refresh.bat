@echo off
set VENV=.venv
set LATEST=0
if "%1"=="--latest" set LATEST=1
if not exist %VENV%\Scripts\python.exe (
    echo Virtual environment not found. Run install.sh first.
    exit /b 1
)
set RUNNING=0
for /f %%p in ('powershell -NoProfile -Command "Get-CimInstance Win32_Process ^| Where-Object { $_.CommandLine -match 'manage.py runserver' } ^| Select-Object -First 1 -ExpandProperty ProcessId"') do set RUNNING=1
if %RUNNING%==1 (
    powershell -NoProfile -Command "Get-CimInstance Win32_Process ^| Where-Object { $_.CommandLine -match 'manage.py runserver' } ^| ForEach-Object { Stop-Process -Id $_.ProcessId }"
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
if %RUNNING%==1 (
    start "" "%~dp0start.bat" --reload
)
