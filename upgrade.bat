@echo off
setlocal
set "BASE_DIR=%~dp0"
set "PIP_HELPER=%BASE_DIR%scripts\helpers\pip_install.py"
cd /d "%BASE_DIR%"

git pull --rebase

if not exist .venv\Scripts\python.exe (
    echo Virtual environment not found. Run install.sh or install.bat first.
    exit /b 1
)

set VENV=.venv
set REQ=requirements.txt
set MD5=requirements.md5
for /f "skip=1 tokens=1" %%h in ('certutil -hashfile %REQ% MD5') do if not defined NEW_HASH set NEW_HASH=%%h
if exist %MD5% (
    set /p STORED_HASH=<%MD5%
)
if /I not "%NEW_HASH%"=="%STORED_HASH%" (
    if exist "%PIP_HELPER%" (
        %VENV%\Scripts\python.exe "%PIP_HELPER%" -r %REQ%
    ) else (
        %VENV%\Scripts\python.exe -m pip install -r %REQ%
    )
    echo %NEW_HASH%>%MD5%
) else (
    echo Requirements unchanged. Skipping installation.
)

:end
endlocal
