@echo off
setlocal EnableExtensions EnableDelayedExpansion
set "BASE_DIR=%~dp0"
set "PIP_HELPER=%BASE_DIR%scripts\helpers\pip_install.py"
set "LOCK_DIR=%BASE_DIR%\.locks"
set "BRANCH="
set "CHANNEL=stable"
set "TARGET_VERSION="
set "TARGET_REVISION="
set "TARGET_TAG="
set "RESOLVED_REVISION="
set "RESOLVED_VERSION="
cd /d "%BASE_DIR%"

:parse_args
if "%~1"=="" goto args_done

if "%~1"=="--branch" (
    if "%~2"=="" (
        echo --branch requires an argument
        exit /b 1
    )
    set "BRANCH=%~2"
    shift
    shift
    goto parse_args
)

if "%~1"=="--target-version" (
    if "%~2"=="" (
        echo --target-version requires an argument
        exit /b 1
    )
    set "TARGET_VERSION=%~2"
    shift
    shift
    goto parse_args
)

if "%~1"=="--target-revision" (
    if "%~2"=="" (
        echo --target-revision requires an argument
        exit /b 1
    )
    set "TARGET_REVISION=%~2"
    shift
    shift
    goto parse_args
)

if "%~1"=="--target-tag" (
    if "%~2"=="" (
        echo --target-tag requires an argument
        exit /b 1
    )
    set "TARGET_TAG=%~2"
    shift
    shift
    goto parse_args
)

if "%~1"=="--latest" (
    set "CHANNEL=unstable"
    shift
    goto parse_args
)

if "%~1"=="--unstable" (
    set "CHANNEL=unstable"
    shift
    goto parse_args
)

if "%~1"=="-l" (
    set "CHANNEL=unstable"
    shift
    goto parse_args
)

if "%~1"=="-t" (
    set "CHANNEL=unstable"
    shift
    goto parse_args
)

if "%~1"=="--force" (
    shift
    goto parse_args
)

if "%~1"=="-f" (
    shift
    goto parse_args
)

if "%~1"=="--start" (
    shift
    goto parse_args
)

if "%~1"=="-s" (
    shift
    goto parse_args
)

if "%~1"=="--main" (
    set "BRANCH=main"
    shift
    goto parse_args
)

shift
goto parse_args

:args_done

if /I "%CHANNEL%"=="unstable" (
    if defined TARGET_VERSION (
        echo Pinned release targets cannot be combined with --latest/--unstable.
        exit /b 1
    )
    if defined TARGET_REVISION (
        echo Pinned release targets cannot be combined with --latest/--unstable.
        exit /b 1
    )
    if defined TARGET_TAG (
        echo Pinned release targets cannot be combined with --latest/--unstable.
        exit /b 1
    )
)

if defined BRANCH (
    git show-ref --verify --quiet "refs/heads/%BRANCH%" >nul 2>&1
    if %errorlevel% equ 0 (
        git switch "%BRANCH%" >nul 2>&1
    ) else (
        git show-ref --verify --quiet "refs/remotes/origin/%BRANCH%" >nul 2>&1
        if %errorlevel% equ 0 (
            git switch -c "%BRANCH%" "origin/%BRANCH%" >nul 2>&1
        ) else (
            echo Requested branch %BRANCH% not found locally or on origin; continuing without switching.
        )
    )
)

if defined TARGET_TAG (
    git fetch origin "refs/tags/%TARGET_TAG%:refs/tags/%TARGET_TAG%"
    if errorlevel 1 (
        echo Unable to fetch release tag %TARGET_TAG% from origin.
        exit /b 1
    )
    for /f "delims=" %%r in ('git rev-parse "refs/tags/%TARGET_TAG%^{commit}" 2^>nul') do set "RESOLVED_REVISION=%%r"
) else if defined TARGET_REVISION (
    if defined BRANCH (
        git fetch origin "%BRANCH%" >nul 2>&1
    ) else (
        git fetch origin main >nul 2>&1
    )
    for /f "delims=" %%r in ('git rev-parse "%TARGET_REVISION%^{commit}" 2^>nul') do set "RESOLVED_REVISION=%%r"
)

if defined RESOLVED_REVISION (
    if defined TARGET_REVISION (
        if not "!RESOLVED_REVISION!"=="!TARGET_REVISION!" (
            echo Pinned release target resolved to !RESOLVED_REVISION!, expected !TARGET_REVISION!.
            exit /b 1
        )
    )
    for /f "delims=" %%v in ('git show "%RESOLVED_REVISION%:VERSION" 2^>nul') do set "RESOLVED_VERSION=%%v"
    if not defined RESOLVED_VERSION (
        echo Pinned release target %RESOLVED_REVISION% does not contain VERSION.
        exit /b 1
    )
    if defined TARGET_VERSION (
        if not "!RESOLVED_VERSION!"=="!TARGET_VERSION!" (
            echo Pinned release target VERSION is !RESOLVED_VERSION!, expected !TARGET_VERSION!.
            exit /b 1
        )
    )
    git reset --hard "%RESOLVED_REVISION%"
) else (
    if defined TARGET_VERSION (
        echo Pinned release upgrades require --target-tag or --target-revision.
        exit /b 1
    )
    git pull --rebase
)

if not exist .venv\Scripts\python.exe (
    echo Virtual environment not found. Run install.sh or install.bat first.
    exit /b 1
)

set VENV=.venv
set REQ=requirements.txt
set HASH=%LOCK_DIR%\requirements.sha256
if not exist "%LOCK_DIR%" mkdir "%LOCK_DIR%" >nul 2>&1
for /f "skip=1 tokens=1" %%h in ('certutil -hashfile %REQ% SHA256') do if not defined NEW_HASH set NEW_HASH=%%h
if exist %HASH% (
    set /p STORED_HASH=<%HASH%
)
if /I not "%NEW_HASH%"=="%STORED_HASH%" (
    if exist "%PIP_HELPER%" (
        %VENV%\Scripts\python.exe "%PIP_HELPER%" -r %REQ%
    ) else (
        %VENV%\Scripts\python.exe -m pip install -r %REQ%
    )
    echo %NEW_HASH%>%HASH%
) else (
    echo Requirements unchanged. Skipping installation.
)

:end
endlocal
