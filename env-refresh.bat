@echo off
setlocal EnableDelayedExpansion
set "SCRIPT_DIR=%~dp0"
set "PIP_HELPER=%SCRIPT_DIR%scripts\helpers\pip_install.py"
set "BACKUP_DIR=%SCRIPT_DIR%backups"
if /I "%SCRIPT_DIR%"=="%SYSTEMDRIVE%\\" (
    echo Refusing to run from drive root.
    exit /b 1
)
pushd "%SCRIPT_DIR%" >nul
set VENV=%SCRIPT_DIR%\.venv
set LATEST=0
set CLEAN=0
if not defined FAILOVER_CREATED (
    for /f %%b in ('powershell -NoProfile -Command "$d=(Get-Date).ToString(\"yyyyMMdd\"); $i=1; while (Test-Path (\".git/refs/heads/failover-$d-$i\")) { $i++ }; Write-Output \"failover-$d-$i\""') do set BRANCH=%%b
    for /f %%s in ('git stash create') do set STASH=%%s
    if defined STASH (
        git branch !BRANCH! %%STASH%% >nul 2>&1
        git reset --mixed >nul 2>&1
    ) else (
        git branch !BRANCH! >nul 2>&1
    )
    echo Created failover branch !BRANCH!
    if exist db.sqlite3 (
        if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%" >nul 2>&1
        copy /Y db.sqlite3 "%BACKUP_DIR%\!BRANCH!.sqlite3" >nul 2>&1
        if errorlevel 1 (
            echo Failed to create database backup for !BRANCH!.>&2
        ) else (
            echo Saved database backup to backups\!BRANCH!.sqlite3
        )
    )
)
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
if not exist "%VENV%\Scripts\python.exe" (
    echo Virtual environment not found. Run install.sh or install.bat first.
    exit /b 1
)

if %CLEAN%==1 (
    del "%SCRIPT_DIR%db*.sqlite3" >nul 2>&1
)
if exist "%SCRIPT_DIR%\requirements.txt" (
    for /f "skip=1 tokens=1" %%h in ('certutil -hashfile "%SCRIPT_DIR%\requirements.txt" MD5') do (
        if not defined REQ_HASH set REQ_HASH=%%h
    )
    if exist "%SCRIPT_DIR%\requirements.md5" (
        set /p STORED_HASH=<"%SCRIPT_DIR%\requirements.md5"
    )
    if /I not "%REQ_HASH%"=="%STORED_HASH%" (
        if exist "%PIP_HELPER%" (
            "%VENV%\Scripts\python.exe" "%PIP_HELPER%" -r "%SCRIPT_DIR%\requirements.txt"
        ) else (
            "%VENV%\Scripts\python.exe" -m pip install -r "%SCRIPT_DIR%\requirements.txt"
        )
        echo %REQ_HASH%>"%SCRIPT_DIR%\requirements.md5"
    )
)
set "ARGS="
if %LATEST%==1 set "ARGS=%ARGS% --latest"
if %CLEAN%==1 set "ARGS=%ARGS% --clean"
"%VENV%\Scripts\python.exe" "%SCRIPT_DIR%\env-refresh.py" %ARGS% database
popd >nul
