@echo off
set "SCRIPT_DIR=%~dp0"
if /I "%SCRIPT_DIR%"=="%SYSTEMDRIVE%\\" (
    echo Refusing to run from drive root.
    exit /b 1
)
pushd "%SCRIPT_DIR%" >nul
set VENV=%SCRIPT_DIR%\.venv
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
if not exist "%VENV%\Scripts\python.exe" (
    echo Virtual environment not found. Run install.sh or install.bat first.
    exit /b 1
)

set "DB_FILE=%SCRIPT_DIR%db.sqlite3"

if %CLEAN%==1 (
    for %%I in ("%DB_FILE%") do (
        if /I not "%%~nxI"=="db.sqlite3" (
            echo Unexpected database file: %%~fI
            popd >nul
            exit /b 1
        )
        if /I not "%%~dpI"=="%SCRIPT_DIR%" (
            echo Database path outside repository: %%~fI
            popd >nul
            exit /b 1
        )
    )
    if exist "%DB_FILE%" (
        set "BACKUP_DIR=%SCRIPT_DIR%backups"
        if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"
        for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMddHHmmss"') do set "STAMP=%%i"
        copy "%DB_FILE%" "%BACKUP_DIR%\db.sqlite3.%STAMP%.bak" >nul
        del "%DB_FILE%" >nul 2>&1
    )
)
if exist "%SCRIPT_DIR%\requirements.txt" (
    for /f "skip=1 tokens=1" %%h in ('certutil -hashfile "%SCRIPT_DIR%\requirements.txt" MD5') do (
        if not defined REQ_HASH set REQ_HASH=%%h
    )
    if exist "%SCRIPT_DIR%\requirements.md5" (
        set /p STORED_HASH=<"%SCRIPT_DIR%\requirements.md5"
    )
    if /I not "%REQ_HASH%"=="%STORED_HASH%" (
        "%VENV%\Scripts\python.exe" -m pip install -r "%SCRIPT_DIR%\requirements.txt"
        echo %REQ_HASH%>"%SCRIPT_DIR%\requirements.md5"
    )
)
set "ARGS="
if %LATEST%==1 set "ARGS=%ARGS% --latest"
if %CLEAN%==1 set "ARGS=%ARGS% --clean"
"%VENV%\Scripts\python.exe" "%SCRIPT_DIR%\env-refresh.py" %ARGS% database
popd >nul
