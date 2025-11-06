@echo off
setlocal
set "BASE_DIR=%~dp0"
set "PIP_HELPER=%BASE_DIR%scripts\helpers\pip_install.py"
set "BACKUP_DIR=%BASE_DIR%backups"
set "LOCK_DIR=%BASE_DIR%locks"
set "NODE_ROLE=Terminal"
cd /d "%BASE_DIR%"

if exist "%LOCK_DIR%\role.lck" (
    for /f "usebackq tokens=* delims=" %%r in ("%LOCK_DIR%\role.lck") do (
        if not "%%r"=="" (
            set "NODE_ROLE=%%r"
            goto role_determined
        )
    )
)
:role_determined
if not defined NODE_ROLE set "NODE_ROLE=Terminal"

set CREATE_FAILOVER=0
for %%R in (Control Satellite Watchtower Constellation) do (
    if /I "%NODE_ROLE%"=="%%R" set CREATE_FAILOVER=1
)

if "%1"=="--revert" goto revert

if %CREATE_FAILOVER%==1 goto create_failover
echo Skipping failover branch creation for %NODE_ROLE% node.
goto after_failover

:create_failover
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
if exist db.sqlite3 (
    if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%" >nul 2>&1
    copy /Y db.sqlite3 "%BACKUP_DIR%\failover-%DATE%-%COUNT%.sqlite3" >nul 2>&1
    if errorlevel 1 (
        echo Failed to create database backup for failover-%DATE%-%COUNT%.>&2
    ) else (
        echo Saved database backup to backups\failover-%DATE%-%COUNT%.sqlite3
    )
)

:after_failover

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
goto end

:revert
for /f %%b in ('git for-each-ref --format="%%(refname:short)" refs/heads/failover-* ^| sort') do set LAST=%%b
if not defined LAST (
    echo No failover branches found.
    exit /b 1
)
set "BACKUP_FILE=%BACKUP_DIR%\%LAST%.sqlite3"
set "REVERT_SOURCE="
set "TEMP_BACKUP="
if exist "%BACKUP_FILE%" (
    set "REVERT_SOURCE=%BACKUP_FILE%"
) else (
    git show %LAST%:db.sqlite3 > "%TEMP%\db_failover.sqlite3" 2>nul
    if exist "%TEMP%\db_failover.sqlite3" (
        set "REVERT_SOURCE=%TEMP%\db_failover.sqlite3"
        set "TEMP_BACKUP=1"
    )
)
if exist db.sqlite3 (
    for %%I in (db.sqlite3) do set CUR_SIZE=%%~zI
) else (
    set CUR_SIZE=0
)
if defined REVERT_SOURCE (
    for %%I in ("%REVERT_SOURCE%") do set PREV_SIZE=%%~zI
    set /a CUR_KB=(%CUR_SIZE%+1023)/1024
    set /a PREV_KB=(%PREV_SIZE%+1023)/1024
    set /a DIFF=%CUR_KB%-%PREV_KB%
    if %DIFF% lss 0 set /a DIFF=-1*%DIFF%
    if not %DIFF%==0 (
        echo Warning: reverting will replace database (current %CUR_KB%KB vs failover %PREV_KB%KB; diff %DIFF%KB)
        set /p CONFIRM=Proceed? [y/N]:
        if /I not "%CONFIRM%"=="Y" (
            echo Revert cancelled.
            if defined TEMP_BACKUP del "%REVERT_SOURCE%" >nul 2>&1
            exit /b 1
        )
    )
) else (
    echo No database backup found for %LAST%. The database will not be modified.>&2
)
git stash push -u -m "upgrade-revert" >nul 2>&1
git reset --hard %LAST%
if defined REVERT_SOURCE (
    copy /Y "%REVERT_SOURCE%" db.sqlite3 >nul 2>&1
    if errorlevel 1 (
        echo Failed to restore database from "%REVERT_SOURCE%".>&2
    ) else (
        for %%I in ("%REVERT_SOURCE%") do echo Restored database from %%~nxI
    )
)
if defined TEMP_BACKUP del "%REVERT_SOURCE%" >nul 2>&1
exit /b

:end
endlocal
