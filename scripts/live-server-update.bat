@echo off
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"
cd /d "%REPO_ROOT%"

echo Stopping existing development server (if running)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'manage.py runserver' } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -ErrorAction Stop } catch { } }" >nul 2>&1

set "DEFAULT_REMOTE=origin"
set "DEFAULT_BRANCH=main"
set "CURRENT_BRANCH="

for /f "delims=" %%B in ('git rev-parse --abbrev-ref HEAD 2^>nul') do set "CURRENT_BRANCH=%%B"
if /I "%CURRENT_BRANCH%"=="HEAD" set "CURRENT_BRANCH="

set "NEED_FALLBACK=true"
git remote get-url upstream >nul 2>&1
if errorlevel 1 (
    echo No 'upstream' remote configured.
) else if "%CURRENT_BRANCH%"=="" (
    echo Unable to determine the current branch for upstream sync.
) else (
    echo Attempting to update from upstream/%CURRENT_BRANCH%...
    call :sync_remote upstream "%CURRENT_BRANCH%"
    if /I "%SYNC_RESULT%"=="success" (
        set "NEED_FALLBACK=false"
    ) else (
        echo Unable to update from upstream/%CURRENT_BRANCH%. Falling back to %DEFAULT_REMOTE%/%DEFAULT_BRANCH%.
    )
)

if /I "%NEED_FALLBACK%"=="true" (
    echo Using default upstream %DEFAULT_REMOTE%/%DEFAULT_BRANCH%...
    call :sync_remote "%DEFAULT_REMOTE%" "%DEFAULT_BRANCH%"
)

if exist "%REPO_ROOT%\env-refresh.bat" (
    echo Refreshing environment with env-refresh.bat --latest...
    call "%REPO_ROOT%\env-refresh.bat" --latest
    if errorlevel 1 (
        echo Environment refresh failed.
        exit /b 1
    )
) else (
    echo env-refresh.bat not found. Skipping environment refresh.
)

exit /b 0

:sync_remote
set "REMOTE=%~1"
set "BRANCH=%~2"
set "SYNC_RESULT=fail"

git remote get-url %REMOTE% >nul 2>&1
if errorlevel 1 (
    echo Remote '%REMOTE%' not configured. Skipping fetch and pull.
    goto :eof
)

echo Fetching updates from %REMOTE%/%BRANCH%...
git fetch %REMOTE% %BRANCH%
if errorlevel 1 (
    echo Failed to fetch from %REMOTE%/%BRANCH%.
    goto :eof
)

git show-ref --verify --quiet refs/remotes/%REMOTE%/%BRANCH%
if errorlevel 1 (
    echo No matching %REMOTE%/%BRANCH% found. Skipping pull.
    goto :eof
)

if "%CURRENT_BRANCH%"=="" (
    echo Detached HEAD state detected; skipping pull for %REMOTE%/%BRANCH%.
    set "SYNC_RESULT=success"
    goto :eof
)

if /I not "%CURRENT_BRANCH%"=="%BRANCH%" (
    echo Current branch "%CURRENT_BRANCH%" does not match "%BRANCH%". Skipping pull.
    set "SYNC_RESULT=success"
    goto :eof
)

echo Pulling latest commits for %BRANCH% from %REMOTE%...
git pull --ff-only %REMOTE% %BRANCH%
if errorlevel 1 (
    echo Failed to pull from %REMOTE%/%BRANCH%.
    goto :eof
)

set "SYNC_RESULT=success"
goto :eof
