@echo off
setlocal EnableDelayedExpansion
set "SCRIPT_DIR=%~dp0"
if not "%SCRIPT_DIR%"=="" pushd "%SCRIPT_DIR%"

set EXIT_CODE=0
set "VENV=.venv"
if not exist "%VENV%\Scripts\python.exe" (
  echo Virtual environment not found. Run install.sh or install.bat first.
  set EXIT_CODE=1
  goto cleanup
)

if defined WORK_DIR (
  set "WORK_DIR=%WORK_DIR%"
) else (
  set "WORK_DIR=%SCRIPT_DIR%work\pyxel_viewport"
)

if defined PYXEL_RUNNER (
  set "PYXEL_RUNNER=%PYXEL_RUNNER%"
) else (
  set "PYXEL_RUNNER=pyxel"
)

:parse
if "%~1"=="" goto after_parse
if "%~1"=="--work-dir" (
  if "%~2"=="" (
    echo --work-dir requires a path
    set EXIT_CODE=1
    goto cleanup
  )
  set "WORK_DIR=%~2"
  shift
  shift
  goto parse
)
if "%~1"=="--pyxel-runner" (
  if "%~2"=="" (
    echo --pyxel-runner requires a command
    set EXIT_CODE=1
    goto cleanup
  )
  set "PYXEL_RUNNER=%~2"
  shift
  shift
  goto parse
)
if "%~1"=="--help" goto usage_help
if "%~1"=="-h" goto usage_help
echo Unknown option: %~1
set EXIT_CODE=1
goto usage

:usage_help
set EXIT_CODE=0
:usage
echo Usage: %~nx0 [--work-dir PATH] [--pyxel-runner CMD]
echo(
echo Creates or refreshes the Pyxel viewport project in the work directory and launches it immediately.
echo Environment variables WORK_DIR and PYXEL_RUNNER can also override the defaults.
goto cleanup

:after_parse
if exist "%WORK_DIR%" (
  if not exist "%WORK_DIR%\\" (
    echo WORK_DIR must be a directory: %WORK_DIR%
    set EXIT_CODE=1
    goto cleanup
  )
  for /f %%i in ('dir /b "%WORK_DIR%" 2^>nul') do (
    echo Refusing to clear non-empty work directory: %WORK_DIR%
    echo Please provide an empty directory or remove its contents manually.
    set EXIT_CODE=1
    goto cleanup
  )
) else (
  mkdir "%WORK_DIR%" >nul 2>&1
  if errorlevel 1 (
    echo Failed to create work directory: %WORK_DIR%
    set EXIT_CODE=1
    goto cleanup
  )
)

"%VENV%\Scripts\python.exe" manage.py pyxel_viewport --output-dir "%WORK_DIR%" --pyxel-runner "%PYXEL_RUNNER%"
set EXIT_CODE=%ERRORLEVEL%

:cleanup
if not "%SCRIPT_DIR%"=="" popd
exit /b %EXIT_CODE%
