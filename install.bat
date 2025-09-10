@echo off
set "SCRIPT_DIR=%~dp0"
if /I "%SCRIPT_DIR%"=="%SYSTEMDRIVE%\\" (
    echo Refusing to run from drive root.
    exit /b 1
)
pushd "%SCRIPT_DIR%" >nul
set "VENV=%SCRIPT_DIR%\.venv"
if not exist "%VENV%\Scripts\python.exe" (
    python -m venv "%VENV%"
)
"%VENV%\Scripts\python.exe" -m pip install -U pip
"%VENV%\Scripts\python.exe" -m pip install -r "%SCRIPT_DIR%\requirements.txt"
"%VENV%\Scripts\python.exe" manage.py migrate --noinput
call "%SCRIPT_DIR%\env-refresh.bat" --latest
popd >nul
