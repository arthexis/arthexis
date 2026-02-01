@echo off
setlocal EnableDelayedExpansion
set "SCRIPT_DIR=%~dp0"
if /I "%SCRIPT_DIR%"=="%SYSTEMDRIVE%\\" (
    echo Refusing to run from drive root.
    exit /b 1
)
if not "%SCRIPT_DIR%"=="" pushd "%SCRIPT_DIR%" >nul

set "VENV=.venv"
if not exist "%VENV%\Scripts\python.exe" (
    echo Virtual environment not found. Run install.sh or install.bat first.
    set EXIT_CODE=1
    goto cleanup
)

set "LOCK_DIR=.locks"
if not exist "%LOCK_DIR%" mkdir "%LOCK_DIR%"
set "LOG_DIR=logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

set "SERVICE_NAME=arthexis"
set "PORT=8888"
if exist "%LOCK_DIR%\backend_port.lck" (
    for /f %%p in ('findstr /R "^[0-9][0-9]*$" "%LOCK_DIR%\backend_port.lck"') do set "PORT=%%p"
)
set "NSSM="
set "AUTO_START=1"

if "%~1"=="" goto usage
set "ACTION=%~1"
shift

:parse
if "%~1"=="" goto run
if /I "%~1"=="--name" (
    set "SERVICE_NAME=%~2"
    shift
    shift
    goto parse
)
if /I "%~1"=="--port" (
    set "PORT=%~2"
    shift
    shift
    goto parse
)
if /I "%~1"=="--nssm" (
    set "NSSM=%~2"
    shift
    shift
    goto parse
)
if /I "%~1"=="--no-start" (
    set "AUTO_START="
    shift
    goto parse
)
if /I "%~1"=="--help" goto usage
echo Unknown argument: %~1
goto usage

:run
if not defined NSSM (
    for %%N in (nssm.exe) do set "NSSM=%%~$PATH:N"
)
if not defined NSSM (
    echo NSSM not found. Install nssm.exe and ensure it is on PATH or pass --nssm ^<path^>.
    set EXIT_CODE=1
    goto cleanup
)

if /I "%ACTION%"=="install" goto install
if /I "%ACTION%"=="remove" goto remove
if /I "%ACTION%"=="uninstall" goto remove
if /I "%ACTION%"=="start" goto start
if /I "%ACTION%"=="stop" goto stop
if /I "%ACTION%"=="restart" goto restart
if /I "%ACTION%"=="status" goto status
goto usage

:install
sc query "%SERVICE_NAME%" >nul 2>&1
if "%ERRORLEVEL%"=="0" (
    echo Service "%SERVICE_NAME%" already exists.
    set EXIT_CODE=1
    goto cleanup
)

set "CMD=%COMSPEC%"
set "RUNNER_ARGS=/c \"\"%SCRIPT_DIR%start.bat\" --port %PORT%\""

"%NSSM%" install "%SERVICE_NAME%" "%CMD%" %RUNNER_ARGS%
if errorlevel 1 (
    set EXIT_CODE=1
    goto cleanup
)
"%NSSM%" set "%SERVICE_NAME%" AppDirectory "%SCRIPT_DIR%"
"%NSSM%" set "%SERVICE_NAME%" DisplayName "Arthexis Suite (%SERVICE_NAME%)"
"%NSSM%" set "%SERVICE_NAME%" Description "Runs the Arthexis suite web application."
"%NSSM%" set "%SERVICE_NAME%" AppStdout "%SCRIPT_DIR%%LOG_DIR%\\%SERVICE_NAME%-service.log"
"%NSSM%" set "%SERVICE_NAME%" AppStderr "%SCRIPT_DIR%%LOG_DIR%\\%SERVICE_NAME%-service.log"
"%NSSM%" set "%SERVICE_NAME%" AppStopMethodConsole 1
"%NSSM%" set "%SERVICE_NAME%" AppStopMethodWindow 1
"%NSSM%" set "%SERVICE_NAME%" AppStopMethodThreads 1
"%NSSM%" set "%SERVICE_NAME%" Start SERVICE_AUTO_START

>"%LOCK_DIR%\service.lck" echo %SERVICE_NAME%
echo Installed service "%SERVICE_NAME%".
if defined AUTO_START (
    "%NSSM%" start "%SERVICE_NAME%"
)
goto cleanup

:remove
sc query "%SERVICE_NAME%" >nul 2>&1
if not "%ERRORLEVEL%"=="0" (
    echo Service "%SERVICE_NAME%" is not installed.
    set EXIT_CODE=1
    goto cleanup
)
"%NSSM%" stop "%SERVICE_NAME%" >nul 2>&1
"%NSSM%" remove "%SERVICE_NAME%" confirm
if exist "%LOCK_DIR%\service.lck" (
    for /f "usebackq delims=" %%s in ("%LOCK_DIR%\service.lck") do (
        if /I "%%s"=="%SERVICE_NAME%" del /f /q "%LOCK_DIR%\service.lck"
    )
)
echo Removed service "%SERVICE_NAME%".
goto cleanup

:start
"%NSSM%" start "%SERVICE_NAME%"
goto cleanup

:stop
"%NSSM%" stop "%SERVICE_NAME%"
goto cleanup

:restart
"%NSSM%" restart "%SERVICE_NAME%"
goto cleanup

:status
"%NSSM%" status "%SERVICE_NAME%"
goto cleanup

:usage
echo Usage: %~nx0 ^<install^|remove^|start^|stop^|restart^|status^> [--name NAME] [--port PORT] [--nssm PATH] [--no-start]
set EXIT_CODE=1
goto cleanup

:cleanup
if not "%SCRIPT_DIR%"=="" popd >nul
if "%EXIT_CODE%"=="" set EXIT_CODE=0
exit /b %EXIT_CODE%
