@echo off
set SCRIPT_DIR=%~dp0
if not "%SCRIPT_DIR%"=="" pushd "%SCRIPT_DIR%"

set VENV=.venv
if not exist %VENV%\Scripts\python.exe (
    echo Virtual environment not found. Run install.sh or install.bat first.
    set EXIT_CODE=1
    goto cleanup
)

set PORT=8888
if exist .locks\backend_port.lck (
    for /f %%p in ('findstr /R "^[0-9][0-9]*$" .locks\backend_port.lck') do set PORT=%%p
)
set RELOAD=
set DEBUG_MODE=
set SHOW_LEVEL=
:parse
if "%1"=="" goto run
if "%1"=="--port" (
    set PORT=%2
    shift
    shift
    goto parse
)
if "%1"=="--reload" (
    set RELOAD=1
    shift
    goto parse
)
if "%1"=="--debug" (
    set DEBUG_MODE=1
    shift
    goto parse
)
if "%1"=="--show" (
    if "%~2"=="" (
        echo Usage: %0 [--port PORT] [--reload] [--debug] [--show LEVEL]
        set EXIT_CODE=1
        goto cleanup
    )
    set SHOW_LEVEL=%2
    shift
    shift
    goto parse
)
echo Usage: %0 [--port PORT] [--reload] [--debug] [--show LEVEL]
set EXIT_CODE=1
goto cleanup

:run
if defined SHOW_LEVEL (
    call :normalize_level "%SHOW_LEVEL%" NORMALIZED_LEVEL
    if errorlevel 1 (
        echo Invalid log level: %SHOW_LEVEL%
        set EXIT_CODE=1
        goto cleanup
    )
    set SHOW_LEVEL=%NORMALIZED_LEVEL%
)

if /I "%SHOW_LEVEL%"=="DEBUG" set DEBUG_MODE=1
if defined DEBUG_MODE set DEBUG=1

set LOG_DIR=%ARTHEXIS_LOG_DIR%
if "%LOG_DIR%"=="" set LOG_DIR=%SCRIPT_DIR%logs
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
if not defined COMPUTERNAME for /f %%h in ('hostname') do set COMPUTERNAME=%%h
set LOG_FILE=%LOG_DIR%\%COMPUTERNAME%.log

if defined SHOW_LEVEL call :start_log_listener "%LOG_FILE%" %SHOW_LEVEL%

if not defined RELOAD set NORELOAD=--noreload
set STATIC_MD5=staticfiles.md5
set NEW_STATIC_HASH=
for /f "usebackq delims=" %%i in (`%VENV%\Scripts\python.exe scripts\staticfiles_md5.py 2^>nul`) do set NEW_STATIC_HASH=%%i
if errorlevel 1 goto collectstatic_fallback
if not defined NEW_STATIC_HASH goto collectstatic_fallback
if exist %STATIC_MD5% (
    set /p STORED_STATIC_HASH=<%STATIC_MD5%
) else (
    set STORED_STATIC_HASH=
)
if /I "%NEW_STATIC_HASH%"=="%STORED_STATIC_HASH%" (
    echo Static files unchanged. Skipping collectstatic.
    goto startserver
)

:collectstatic_update
%VENV%\Scripts\python.exe manage.py collectstatic --noinput
if errorlevel 1 goto collectstatic_failed
>%STATIC_MD5% echo %NEW_STATIC_HASH%
goto startserver

:collectstatic_fallback
echo Failed to compute static files hash; running collectstatic.
%VENV%\Scripts\python.exe manage.py collectstatic --noinput
if errorlevel 1 goto collectstatic_failed

:startserver
echo Running Django preflight checks once before runserver...
set DJANGO_SUPPRESS_MIGRATION_CHECK=1
set RUNSERVER_SKIP_CHECKS=
%VENV%\Scripts\python.exe manage.py migrate --check
if errorlevel 1 (
    set EXIT_CODE=1
    goto cleanup
)
%VENV%\Scripts\python.exe manage.py check
if errorlevel 1 (
    set EXIT_CODE=1
    goto cleanup
)
set RUNSERVER_SKIP_CHECKS=--skip-checks
%VENV%\Scripts\python.exe manage.py runserver 0.0.0.0:%PORT% %NORELOAD% %RUNSERVER_SKIP_CHECKS%
set EXIT_CODE=%ERRORLEVEL%
goto cleanup

:collectstatic_failed
echo collectstatic failed
set EXIT_CODE=1
goto cleanup

:normalize_level
set "RAW_LEVEL=%~1"
set "OUT_VAR=%~2"
set "LEVEL=%RAW_LEVEL%"
if /I "%LEVEL%"=="warn" set "LEVEL=WARNING"
if /I "%LEVEL%"=="fatal" set "LEVEL=CRITICAL"
set "VALID_LEVEL="
for %%L in (DEBUG INFO WARNING ERROR CRITICAL) do (
    if /I "%%L"=="%LEVEL%" (
        set "LEVEL=%%L"
        set VALID_LEVEL=1
    )
)
if not defined VALID_LEVEL exit /b 1
set "%OUT_VAR%=%LEVEL%"
exit /b 0

:start_log_listener
set "TARGET_LOG=%~1"
set "MIN_LEVEL=%~2"
start "" /B powershell -NoProfile -Command ^
 "$logPath = '%TARGET_LOG%';" ^
 "$minLevel = '%MIN_LEVEL%';" ^
 "$map = @{DEBUG=0;INFO=1;WARNING=2;ERROR=3;CRITICAL=4};" ^
 "if (-not $map.ContainsKey($minLevel)) { Write-Error 'Invalid log level'; exit 1 }" ^
 "if (-not (Test-Path $logPath)) { New-Item -Path $logPath -ItemType File -Force ^| Out-Null }" ^
 "Write-Host \"Streaming log entries from $logPath at level $minLevel or higher...\";" ^
 "$minPriority = $map[$minLevel];" ^
 "Get-Content -Path $logPath -Tail 0 -Wait ^|" ^
 "  ForEach-Object {" ^
 "    $line = $_;" ^
 "    if ($line -match '\\[(?<lvl>[A-Z]+)\\]') {" ^
 "      $lvl = $Matches['lvl'];" ^
 "      if ($map.ContainsKey($lvl) -and $map[$lvl] -lt $minPriority) { return }" ^
 "    }" ^
 "    Write-Host $line" ^
 "  }"
exit /b 0

:cleanup
if not "%SCRIPT_DIR%"=="" popd
exit /b %EXIT_CODE%
