@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PYTHON=%SCRIPT_DIR%\.venv\Scripts\python.exe"
set "HELPER=%SCRIPT_DIR%scripts\gway_imager.py"

if /I "%SCRIPT_DIR%"=="%SYSTEMDRIVE%\" (
    echo Refusing to run from drive root.
    exit /b 1
)

if not exist "%PYTHON%" (
    echo Virtual environment not found. Run install.bat first.
    exit /b 1
)

"%PYTHON%" "%HELPER%" %*
set "EXITCODE=%ERRORLEVEL%"
endlocal & exit /b %EXITCODE%
