@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "BASE_DIR=%~dp0"
if "%BASE_DIR:~-1%"=="\" set "BASE_DIR=%BASE_DIR:~0,-1%"
cd /d "%BASE_DIR%"

set "LOG_DIR=%BASE_DIR%\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set "LOG_FILE=%LOG_DIR%\start.log"

set "PYTHON_BIN=python"
if exist "%BASE_DIR%\.venv\Scripts\python.exe" set "PYTHON_BIN=%BASE_DIR%\.venv\Scripts\python.exe"

set "VENV=.venv"
set "STATUS=0"
if not exist %VENV%\Scripts\python.exe (
    echo Virtual environment not found. Run install.sh or install.bat first.
    set "STATUS=1"
    call :report_failure %STATUS% "verify virtual environment"
    goto finish
)

set "PORT=8000"
set "RELOAD="
set "NORELOAD="
set "LAST_COMMAND="

:parse
if "%1"=="" goto run
if "%1"=="--port" (
    set "PORT=%2"
    shift
    shift
    goto parse
)
if "%1"=="--reload" (
    set "RELOAD=1"
    shift
    goto parse
)
echo Usage: %0 [--port PORT] [--reload]
set "STATUS=1"
goto finish

:run
if not defined RELOAD set "NORELOAD=--noreload"

set "LAST_COMMAND=python manage.py collectstatic --noinput"
powershell -NoProfile -Command ^
  "& { param($logPath) if (!(Test-Path $logPath)) { New-Item -ItemType File -Path $logPath -Force ^| Out-Null } ; & '%VENV%\Scripts\python.exe' 'manage.py' 'collectstatic' '--noinput' 2>&1 ^| Tee-Object -FilePath $logPath -Append ; exit $LASTEXITCODE }" ^
  "%LOG_FILE%"
if errorlevel 1 (
    set "STATUS=%ERRORLEVEL%"
    call :report_failure %STATUS% "!LAST_COMMAND!"
    goto finish
)

set "LAST_COMMAND=python manage.py runserver 0.0.0.0:%PORT% %NORELOAD%"
powershell -NoProfile -Command ^
  "& { param($logPath, $port, $reloadFlag) if (!(Test-Path $logPath)) { New-Item -ItemType File -Path $logPath -Force ^| Out-Null } ; $args = @('manage.py', 'runserver', '0.0.0.0:' + $port); if ($reloadFlag -eq '') { $args += '--noreload' } ; & '%VENV%\Scripts\python.exe' @args 2>&1 ^| Tee-Object -FilePath $logPath -Append ; exit $LASTEXITCODE }" ^
  "%LOG_FILE%" "%PORT%" "%RELOAD%"
set "STATUS=%ERRORLEVEL%"
if %STATUS% neq 0 (
    call :report_failure %STATUS% "!LAST_COMMAND!"
)

goto finish

:report_failure
set "FAIL_EXIT=%~1"
set "FAIL_COMMAND=%~2"
>&2 echo start.bat failed with exit code %FAIL_EXIT% while running: %FAIL_COMMAND%
powershell -NoProfile -Command "if (Test-Path '%LOG_FILE%') { Write-Output '----- Last 100 lines from %LOG_FILE% -----' ; Get-Content -Path '%LOG_FILE%' -Tail 100 ; Write-Output '----------------------------------------' }" 1>&2

set "VERSION_DATA=unknown"
if exist "%BASE_DIR%\VERSION" (
    set /p VERSION_DATA=<"%BASE_DIR%\VERSION"
)

set "REVISION="
for /f "usebackq delims=" %%R in (`"%PYTHON_BIN%" -c "from utils.revision import get_revision; print(get_revision(), end='')" 2^>nul`) do set "REVISION=%%R"
if not defined REVISION (
    for /f "usebackq delims=" %%R in (`git rev-parse HEAD 2^>nul`) do set "REVISION=%%R"
)

"%PYTHON_BIN%" manage.py report_issue --source start --command "%FAIL_COMMAND%" --exit-code %FAIL_EXIT% --host "%COMPUTERNAME%" --app-version "%VERSION_DATA%" --revision "%REVISION%" --log-file "%LOG_FILE%" >nul 2>nul

exit /b %FAIL_EXIT%

:finish
endlocal & exit /b %STATUS%
