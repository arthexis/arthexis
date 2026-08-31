@echo off
setlocal EnableExtensions EnableDelayedExpansion
set "BASE_DIR=%~dp0"

if defined ARTHEXIS_VENV_DIR (
    call :try_venv "%ARTHEXIS_VENV_DIR%" %*
    if not errorlevel 9009 exit /b
)

if defined ARTHEXIS_ENV_ROOT (
    if not defined ARTHEXIS_VENV_DIR (
        call :qa_cache_key_preferred
        if "!QA_CACHE_KEY_PREFERRED!"=="1" (
            call :resolve_cached_venv --include-ci
            if defined RESOLVED_VENV_DIR (
                call :try_venv "!RESOLVED_VENV_DIR!" %*
                if not errorlevel 9009 exit /b
            )
            call :resolve_cached_venv
            if defined RESOLVED_VENV_DIR (
                call :try_venv "!RESOLVED_VENV_DIR!" %*
                if not errorlevel 9009 exit /b
            )
        ) else (
            call :resolve_cached_venv
            if defined RESOLVED_VENV_DIR (
                call :try_venv "!RESOLVED_VENV_DIR!" %*
                if not errorlevel 9009 exit /b
            )
            call :resolve_cached_venv --include-ci
            if defined RESOLVED_VENV_DIR (
                call :try_venv "!RESOLVED_VENV_DIR!" %*
                if not errorlevel 9009 exit /b
            )
        )
    )
)

call :try_venv "%BASE_DIR%.venv" %*
if not errorlevel 9009 exit /b

call :try_venv "%BASE_DIR%venv" %*
if not errorlevel 9009 exit /b

echo No project virtual environment Python was found. >&2
echo. >&2
echo Lookup order: >&2
echo   ARTHEXIS_VENV_DIR >&2
echo   ARTHEXIS_ENV_ROOT dependency-hash cache >&2
echo   .venv >&2
echo   venv >&2
echo. >&2
echo Bootstrap the environment first: >&2
echo   install.bat >&2
echo. >&2
echo Then rerun your command, for example: >&2
echo   py.bat manage.py test run -- apps/sites >&2
exit /b 1

:try_venv
set "CANDIDATE=%~1"
shift /1
set "FORWARDED_ARGS="
:collect_forwarded_args
if "%~1"=="" goto run_venv_python
set "FORWARDED_ARGS=!FORWARDED_ARGS! "%~1""
shift /1
goto collect_forwarded_args

:run_venv_python
if exist "%CANDIDATE%\Scripts\python.exe" (
    "%CANDIDATE%\Scripts\python.exe" !FORWARDED_ARGS!
    exit /b
)
if exist "%CANDIDATE%\bin\python" (
    "%CANDIDATE%\bin\python" !FORWARDED_ARGS!
    exit /b
)
exit /b 9009

:qa_cache_key_preferred
set "QA_CACHE_KEY_PREFERRED=0"
for %%V in (1 true yes) do (
    if /I "%ARTHEXIS_INCLUDE_QA_REQUIREMENTS%"=="%%V" set "QA_CACHE_KEY_PREFERRED=1"
    if /I "%ARTHEXIS_INSTALL_PREVIEW_DEPS%"=="%%V" set "QA_CACHE_KEY_PREFERRED=1"
)
exit /b 0

:resolve_cached_venv
set "RESOLVED_VENV_DIR="
set "VENV_ARGS="%BASE_DIR%.""
if /I "%~1"=="--include-ci" set "VENV_ARGS=%VENV_ARGS% --include-ci"
set "INCLUDE_HARDWARE=0"
for %%V in (1 true yes) do (
    if /I "%ARTHEXIS_INSTALL_HARDWARE_DEPS%"=="%%V" set "INCLUDE_HARDWARE=1"
    if /I "%ARTHEXIS_INCLUDE_HARDWARE_REQUIREMENTS%"=="%%V" set "INCLUDE_HARDWARE=1"
)
set "LCD_LOCK=%ARTHEXIS_LCD_LOCK%"
if not defined LCD_LOCK set "LCD_LOCK=lcd_screen.lck"
set "RFID_SERVICE_LOCK=%ARTHEXIS_RFID_SERVICE_LOCK%"
if not defined RFID_SERVICE_LOCK set "RFID_SERVICE_LOCK=rfid-service.lck"
set "RFID_LOCK=%ARTHEXIS_RFID_LOCK%"
if not defined RFID_LOCK set "RFID_LOCK=rfid.lck"
if exist "%BASE_DIR%.locks\control.lck" set "INCLUDE_HARDWARE=1"
if exist "%BASE_DIR%.locks\%LCD_LOCK%" set "INCLUDE_HARDWARE=1"
if exist "%BASE_DIR%.locks\%RFID_SERVICE_LOCK%" set "INCLUDE_HARDWARE=1"
if exist "%BASE_DIR%.locks\%RFID_LOCK%" set "INCLUDE_HARDWARE=1"
if exist "%BASE_DIR%.locks\role.lck" (
    findstr /x "Control" "%BASE_DIR%.locks\role.lck" >nul 2>&1
    if not errorlevel 1 set "INCLUDE_HARDWARE=1"
)
if "%INCLUDE_HARDWARE%"=="1" set "VENV_ARGS=%VENV_ARGS% --include-hardware"
for %%P in (python.exe py.exe) do (
    if not defined RESOLVED_VENV_DIR (
        for /f "usebackq delims=" %%V in (`%%P "%BASE_DIR%scripts\helpers\venv_path.py" %VENV_ARGS% 2^>nul`) do (
            set "RESOLVED_VENV_DIR=%%V"
        )
    )
)
exit /b 0
