@echo off
set SCRIPT_DIR=%~dp0
if not "%SCRIPT_DIR%"=="" pushd "%SCRIPT_DIR%"

set VENV=.venv
set LOCK_DIR=.locks
if not exist "%LOCK_DIR%" mkdir "%LOCK_DIR%"
if not exist %VENV%\Scripts\python.exe (
    echo Virtual environment not found. Run install.sh or install.bat first.
    set EXIT_CODE=1
    goto cleanup
)

set PORT=8888
if exist %LOCK_DIR%\backend_port.lck (
    for /f %%p in ('findstr /R "^[0-9][0-9]*$" %LOCK_DIR%\backend_port.lck') do set PORT=%%p
)

set ALL=
set FORCE=
:parse
if "%1"=="" goto run
if "%1"=="--all" (
    set ALL=1
    shift
    goto parse
)
if "%1"=="--force" (
    set FORCE=1
    shift
    goto parse
)
set PORT=%1
shift
goto parse

:run
set CHARGING_LOCK=%LOCK_DIR%\charging.lck

if not "%ARTHEXIS_STOP_DB_PATH%"=="" (
    set LOCK_MAX_AGE=%CHARGING_LOCK_MAX_AGE_SECONDS%
    if "%LOCK_MAX_AGE%"=="" set LOCK_MAX_AGE=300
    set STALE_AFTER=%CHARGING_SESSION_STALE_AFTER_SECONDS%
    if "%STALE_AFTER%"=="" set STALE_AFTER=86400
    for /f "tokens=1,2" %%a in ('"%VENV%\Scripts\python.exe" scripts\charging_session_counts.py') do (
        set ACTIVE_COUNT=%%a
        set STALE_COUNT=%%b
    )
    if "%ACTIVE_COUNT%"=="" set ACTIVE_COUNT=0
    if "%STALE_COUNT%"=="" set STALE_COUNT=0

    if not "%ACTIVE_COUNT%"=="0" (
        if exist "%CHARGING_LOCK%" (
            call :lock_age "%CHARGING_LOCK%" LOCK_AGE
            if %LOCK_MAX_AGE% GEQ 0 if %LOCK_AGE% GTR %LOCK_MAX_AGE% (
                echo Charging lock appears stale; continuing shutdown.
            ) else if %STALE_COUNT% GTR 0 (
                echo Found %STALE_COUNT% session(s) without recent activity; removing charging lock.
                del /f /q "%CHARGING_LOCK%"
            ) else if defined FORCE (
                echo Active charging sessions detected but --force supplied; continuing shutdown.
            ) else (
                echo Active charging sessions detected; aborting stop.
                set EXIT_CODE=1
                goto cleanup
            )
        ) else (
            echo Active charging sessions detected but no charging lock present; assuming the sessions are stale.
        )
    )
)

call :stop_pidfile "%LOCK_DIR%\django.pid" "Django server"
call :stop_pidfile "%LOCK_DIR%\celery_worker.pid" "Celery worker"
call :stop_pidfile "%LOCK_DIR%\celery_beat.pid" "Celery beat"
if /I not "%ARTHEXIS_SKIP_LCD_STOP%"=="1" if /I not "%ARTHEXIS_SKIP_LCD_STOP%"=="true" (
    call :stop_pidfile "%LOCK_DIR%\lcd.pid" "LCD screen"
)

if defined ALL (
    call :kill_by_commandline "manage.py runserver"
) else (
    call :kill_by_commandline "manage.py runserver 0.0.0.0:%PORT%"
)
call :kill_by_commandline "celery -A config"
call :kill_by_commandline "manage.py rfid_service"

goto cleanup

:stop_pidfile
set "PID_FILE=%~1"
set "NAME=%~2"
if not exist "%PID_FILE%" exit /b 0
set /p PID=<"%PID_FILE%"
if "%PID%"=="" (
    del /f /q "%PID_FILE%"
    exit /b 0
)
set "FOUND_PID="
for /f "tokens=2 delims==" %%p in ('wmic process where "ProcessId=%PID%" get ProcessId /value ^| findstr /R "ProcessId"') do set FOUND_PID=%%p
if defined FOUND_PID (
    if not "%NAME%"=="" echo Stopping %NAME% process (PID %PID%) from %PID_FILE%
    taskkill /PID %PID% /T /F >nul 2>&1
)
del /f /q "%PID_FILE%"
set "FOUND_PID="
exit /b 0

:kill_by_commandline
set "MATCH=%~1"
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -like '*%MATCH%*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
exit /b 0

:lock_age
set "LOCK_FILE=%~1"
set "OUT_VAR=%~2"
for /f %%i in ('powershell -NoProfile -Command "$item=Get-Item -LiteralPath ''%LOCK_FILE%''; [int]((Get-Date).ToUniversalTime()-$item.LastWriteTimeUtc).TotalSeconds"') do set "%OUT_VAR%=%%i
exit /b 0

:cleanup
if not "%SCRIPT_DIR%"=="" popd
if "%EXIT_CODE%"=="" set EXIT_CODE=0
exit /b %EXIT_CODE%
