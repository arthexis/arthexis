@echo off
setlocal
set BASE_DIR=%~dp0
cd /d %BASE_DIR%

if "%1"=="--revert" goto revert

for /f %%i in ('powershell -NoProfile -Command "(Get-Date).ToString(\"yyyyMMdd\")"') do set DATE=%%i
set COUNT=1
:find_branch
git rev-parse --verify "failover-%DATE%-%COUNT%" >nul 2>&1 && (set /a COUNT+=1 & goto find_branch)
for /f %%s in ('git stash create') do set STASH=%%s
if defined STASH (
    git branch "failover-%DATE%-%COUNT%" %%STASH%% >nul 2>&1
    git reset --hard >nul 2>&1
) else (
    git branch "failover-%DATE%-%COUNT%" >nul 2>&1
)
echo Created failover branch failover-%DATE%-%COUNT%

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
    %VENV%\Scripts\python.exe -m pip install -r %REQ%
    echo %NEW_HASH%>%MD5%
) else (
    echo Requirements unchanged. Skipping installation.
)
goto end

:revert
for /f %%b in ('git for-each-ref --format="%%(refname:short)" refs/heads/failover-* ^| sort') do set LAST=%%b
if not defined LAST (
    echo No failover branches found.
    exit /b 1
)
git show %LAST%:db.sqlite3 > "%TEMP%\db_failover.sqlite3" 2>nul
if exist db.sqlite3 (
    for %%I in (db.sqlite3) do set CUR_SIZE=%%~zI
) else (
    set CUR_SIZE=0
)
if exist "%TEMP%\db_failover.sqlite3" (
    for %%I in ("%TEMP%\db_failover.sqlite3") do set PREV_SIZE=%%~zI
    set /a CUR_KB=(%CUR_SIZE%+1023)/1024
    set /a PREV_KB=(%PREV_SIZE%+1023)/1024
    set /a DIFF=%CUR_KB%-%PREV_KB%
    if %DIFF% lss 0 set /a DIFF=-1*%DIFF%
    if not %DIFF%==0 (
        echo Warning: reverting will replace database (current %CUR_KB%KB vs failover %PREV_KB%KB; diff %DIFF%KB)
        set /p CONFIRM=Proceed? [y/N]:
        if /I not "%CONFIRM%"=="Y" (
            echo Revert cancelled.
            del "%TEMP%\db_failover.sqlite3" >nul 2>&1
            exit /b 1
        )
    )
)
git stash push -u -m "upgrade-revert" >nul 2>&1
git reset --hard %LAST%
if exist "%TEMP%\db_failover.sqlite3" (
    move /Y "%TEMP%\db_failover.sqlite3" db.sqlite3 >nul 2>&1
) else (
    del "%TEMP%\db_failover.sqlite3" >nul 2>&1
)
exit /b

:end
endlocal
