@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "USER_IDENTIFIER=arthexis"
set "SERVICE_DIR=arthexis"
set "EXPIRY_MINUTES=60"
set "CHECK_ONLY="
set "PRINT_ONLY="
set "CREATE_USER="

if not defined GW_SSH_TARGET set "GW_SSH_TARGET=gway"
if not defined GW_REMOTE_SSH set "GW_REMOTE_SSH=/home/arthe/.local/bin/usb-ssh"
if not defined GW_PROD_TARGET set "GW_PROD_TARGET=ubuntu@arthexis.com"

:parse
if "%~1"=="" goto run
if /I "%~1"=="--user" (
    if "%~2"=="" (
        echo Missing value for --user.
        goto usage_error
    )
    set "USER_IDENTIFIER=%~2"
    shift
    shift
    goto parse
)
if /I "%~1"=="--service" (
    if "%~2"=="" (
        echo Missing value for --service.
        goto usage_error
    )
    set "SERVICE_DIR=%~2"
    shift
    shift
    goto parse
)
if /I "%~1"=="--expiry" (
    if "%~2"=="" (
        echo Missing value for --expiry.
        goto usage_error
    )
    set "EXPIRY_MINUTES=%~2"
    shift
    shift
    goto parse
)
if /I "%~1"=="--create" (
    set "CREATE_USER=1"
    shift
    goto parse
)
if /I "%~1"=="--check" (
    set "CHECK_ONLY=1"
    shift
    goto parse
)
if /I "%~1"=="--print" (
    set "PRINT_ONLY=1"
    shift
    goto parse
)
if /I "%~1"=="--help" goto usage
if /I "%~1"=="-h" goto usage

echo Unknown option: %~1
goto usage_error

:run
call :validate_identifier "%USER_IDENTIFIER%" || goto usage_error
call :validate_service "%SERVICE_DIR%" || goto usage_error
call :validate_expiry "%EXPIRY_MINUTES%" || goto usage_error
set /a EXPIRY_SECONDS=%EXPIRY_MINUTES% * 60

set "CREATE_ARG="
if defined CREATE_USER set "CREATE_ARG=--create"

if defined CHECK_ONLY (
    set "REMOTE_COMMAND=cd /home/ubuntu/%SERVICE_DIR% && test -x .venv/bin/python && test -f manage.py && .venv/bin/python manage.py help password >/dev/null"
) else (
    set "REMOTE_COMMAND=cd /home/ubuntu/%SERVICE_DIR% && .venv/bin/python manage.py password %USER_IDENTIFIER% --temporary --expires-in %EXPIRY_SECONDS% --allow-change %CREATE_ARG%"
)

set "SSH_COMMAND=ssh %GW_SSH_TARGET% "%GW_REMOTE_SSH%" %GW_PROD_TARGET% "%REMOTE_COMMAND%""

if defined PRINT_ONLY (
    echo %SSH_COMMAND%
    exit /b 0
)

if defined CHECK_ONLY (
    echo Checking GWAY access, remote service directory, and password command for %SERVICE_DIR%...
) else (
    if defined CREATE_USER (
        echo Generating temporary password for %USER_IDENTIFIER% on %SERVICE_DIR% via gway, creating user if missing...
    ) else (
        echo Generating temporary password for %USER_IDENTIFIER% on %SERVICE_DIR% via gway...
    )
)

%SSH_COMMAND%
exit /b %ERRORLEVEL%

:validate_identifier
echo(%~1| findstr /R "^[A-Za-z0-9_.@+-][A-Za-z0-9_.@+-]*$" >nul
if errorlevel 1 (
    echo Invalid --user value: %~1
    exit /b 1
)
exit /b 0

:validate_service
echo(%~1| findstr /R "^[A-Za-z0-9_-][A-Za-z0-9_-]*$" >nul
if errorlevel 1 (
    echo Invalid --service value: %~1
    exit /b 1
)
exit /b 0

:validate_expiry
echo(%~1| findstr /R "^[0-9][0-9]*$" >nul
if errorlevel 1 (
    echo Invalid --expiry value: %~1
    exit /b 1
)
if %~1 LSS 1 (
    echo --expiry must be at least 1 minute.
    exit /b 1
)
if %~1 GTR 4320 (
    echo --expiry must be no more than 4320 minutes.
    exit /b 1
)
exit /b 0

:usage_error
call :usage
exit /b 1

:usage
echo Usage: gway-temp-pass.bat [--user ^<identifier^>] [--service ^<dir^>] [--expiry ^<minutes^>] [--create] [--check] [--print]
echo.
echo Generates an Arthexis temporary password on the arthexis.com host through GWAY.
echo The default user is arthexis, default service directory is arthexis, and
echo default expiry is 60 minutes.
echo.
echo Examples:
echo   gway-temp-pass.bat
echo   gway-temp-pass.bat --user alice
echo   gway-temp-pass.bat --user alice@example.com --expiry 30
echo   gway-temp-pass.bat --service audi --user arthexis --expiry 120
echo   gway-temp-pass.bat --service porsche --create
echo   gway-temp-pass.bat --service porsche --check
echo   gway-temp-pass.bat --print --user arthexis --expiry 60
echo.
echo Options:
echo   --user ^<identifier^>  Username, email, or numeric id. Default: arthexis
echo   --service ^<dir^>      Remote /home/ubuntu/^<dir^> instance. Default: arthexis
echo   --expiry ^<minutes^>   Temporary password lifetime, 1..4320 minutes.
echo   --create              Create the user remotely if it does not exist.
echo   --check               Verify GWAY, remote service directory, and password command.
echo   --print               Print the SSH command without running it.
echo.
echo Overrides:
echo   set GW_SSH_TARGET=gway
echo   set GW_REMOTE_SSH=/home/arthe/.local/bin/usb-ssh
echo   set GW_PROD_TARGET=ubuntu@arthexis.com
exit /b 0
