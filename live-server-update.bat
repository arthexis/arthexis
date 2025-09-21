@echo off
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

echo Stopping existing development server (if running)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'manage.py runserver' } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -ErrorAction Stop } catch { } }" >nul 2>&1

git remote get-url upstream >nul 2>&1
if errorlevel 1 (
    echo No 'upstream' remote configured. Skipping fetch and pull.
    exit /b 0
)

for /f "delims=" %%B in ('git rev-parse --abbrev-ref HEAD') do set BRANCH=%%B

if "%BRANCH%"=="" (
    echo Unable to determine the current branch. Skipping fetch and pull.
    exit /b 0
)

echo Fetching updates from upstream...
git fetch upstream
if errorlevel 1 (
    echo Failed to fetch from upstream.
    exit /b 1
)

git show-ref --verify --quiet refs/remotes/upstream/%BRANCH%
if errorlevel 1 (
    echo No matching upstream branch for %BRANCH%. Skipping pull.
    exit /b 0
)

echo Pulling latest commits for %BRANCH%...
git pull --ff-only upstream %BRANCH%
if errorlevel 1 (
    echo Failed to pull from upstream.
    exit /b 1
)

if exist "%SCRIPT_DIR%env-refresh.bat" (
    echo Refreshing environment with env-refresh.bat --latest...
    call "%SCRIPT_DIR%env-refresh.bat" --latest
    if errorlevel 1 (
        echo Environment refresh failed.
        exit /b 1
    )
) else (
    echo env-refresh.bat not found. Skipping environment refresh.
)

exit /b 0
