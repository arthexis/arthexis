@echo off
setlocal
set "BASE_DIR=%~dp0"

if exist "%BASE_DIR%.venv\Scripts\python.exe" (
    "%BASE_DIR%.venv\Scripts\python.exe" %*
    exit /b %errorlevel%
)

if exist "%BASE_DIR%venv\Scripts\python.exe" (
    "%BASE_DIR%venv\Scripts\python.exe" %*
    exit /b %errorlevel%
)

echo No project virtual environment Python was found. >&2
echo. >&2
echo Expected one of: >&2
echo   .venv\Scripts\python.exe >&2
echo   venv\Scripts\python.exe >&2
echo. >&2
echo Bootstrap the environment first: >&2
echo   install.bat >&2
echo. >&2
echo Then rerun your command, for example: >&2
echo   py.bat manage.py test run -- apps/sites >&2
exit /b 1
