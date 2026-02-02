@echo off
setlocal EnableDelayedExpansion
set VENV=.venv
if not exist %VENV%\Scripts\python.exe (
    echo Virtual environment not found. Run install.sh or install.bat first.
    exit /b 1
)
if "%~1"=="" (
    echo Available Django management commands:
    %VENV%\Scripts\python.exe manage.py help --commands
    echo.
    echo Usage: %~nx0 ^<command^> [args...]
    exit /b 0
)
set COMMAND_RAW=%1
set COMMAND=%1
set COMMAND=%COMMAND:-=_%
shift
set COMMANDS=
for /f "usebackq delims=" %%A in (`%VENV%\Scripts\python.exe manage.py help --commands`) do (
    echo "%%A" | findstr /B /C:"[" >nul || (
        for %%B in (%%A) do (
            set COMMANDS=!COMMANDS! %%B
        )
    )
)
set FOUND=
for %%C in (!COMMANDS!) do (
    if /I "%%C"=="%COMMAND%" set FOUND=1
)
if not defined FOUND (
    set PREFIX_MATCHES=
    set CONTAINS_MATCHES=
    for %%C in (!COMMANDS!) do (
        echo %%C | findstr /I /B /C:"%COMMAND%" >nul && (
            set PREFIX_MATCHES=!PREFIX_MATCHES! %%C
        ) || (
            echo %%C | findstr /I /C:"%COMMAND%" >nul && set CONTAINS_MATCHES=!CONTAINS_MATCHES! %%C
        )
    )
    echo No exact match for "%COMMAND_RAW%".
    if defined PREFIX_MATCHES (
        echo Possible commands:
        for %%C in (!PREFIX_MATCHES!) do echo   %%C
    )
    if defined CONTAINS_MATCHES (
        if not defined PREFIX_MATCHES echo Possible commands:
        for %%C in (!CONTAINS_MATCHES!) do echo   %%C
    )
    if not defined PREFIX_MATCHES if not defined CONTAINS_MATCHES (
        echo Run %~nx0 with no arguments to see available commands.
    )
    exit /b 1
)
%VENV%\Scripts\python.exe manage.py %COMMAND% %*
endlocal
