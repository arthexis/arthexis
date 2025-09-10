@echo off
setlocal
set BASE_DIR=%~dp0
cd /d %BASE_DIR%

set STASHED=0
for /f %%i in ('git status --porcelain') do (
    set STASHED=1
    goto do_stash
)
:do_stash
if %STASHED%==1 (
    echo Warning: stashing local changes before upgrade
    git stash push -u -m "auto-upgrade %date% %time%" >nul 2>&1
)

git pull --rebase

if %STASHED%==1 (
    git stash pop || rem ignore errors
)

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
    %VENV%\Scripts\python.exe -m pip install -r %REQ%
    echo %NEW_HASH%>%MD5%
) else (
    echo Requirements unchanged. Skipping installation.
)
endlocal
