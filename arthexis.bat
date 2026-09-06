@echo off
REM Usage: arthexis.bat <command> [args...]
REM Native Windows entrypoint for the shared Arthexis lifecycle dispatcher.
setlocal

where py >nul 2>&1
if not errorlevel 1 goto :use_py

where python >nul 2>&1
if not errorlevel 1 goto :use_python

echo arthexis: Python 3 is required. 1>&2
exit /b 127

:use_py
py -3 "%~dp0arthexis.py" %*
exit /b %errorlevel%

:use_python
python "%~dp0arthexis.py" %*
exit /b %errorlevel%
