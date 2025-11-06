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
if exist locks\backend_port.lck (
    for /f %%p in ('findstr /R "^[0-9][0-9]*$" locks\backend_port.lck') do set PORT=%%p
)
set RELOAD=
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
echo Usage: %0 [--port PORT] [--reload]
set EXIT_CODE=1
goto cleanup

:run
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
%VENV%\Scripts\python.exe manage.py runserver 0.0.0.0:%PORT% %NORELOAD%
set EXIT_CODE=%ERRORLEVEL%
goto cleanup

:collectstatic_failed
echo collectstatic failed
set EXIT_CODE=1
goto cleanup

:cleanup
if not "%SCRIPT_DIR%"=="" popd
exit /b %EXIT_CODE%
