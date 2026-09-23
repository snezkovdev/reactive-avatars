@echo off
setlocal
cd /d "%~dp0"
title Reactive Avatars 2

set "PY_CMD="
where py >nul 2>nul
if not errorlevel 1 set "PY_CMD=py -3"

if not defined PY_CMD (
    where python >nul 2>nul
    if not errorlevel 1 set "PY_CMD=python"
)

if not defined PY_CMD (
    echo.
    echo Python was not found.
    echo Download Python 3.11 or newer from:
    echo https://www.python.org/downloads/windows/
    echo Enable "Add Python to PATH" during installation.
    echo Then close this window and run START.bat again.
    echo.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo First run: creating the local environment...
    %PY_CMD% -m venv ".venv"
    if errorlevel 1 goto error
)

echo Installing or checking required components...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r "requirements.txt"
if errorlevel 1 goto error

echo Starting Reactive Avatars 2...
".venv\Scripts\python.exe" "main.py"
if errorlevel 1 goto error
exit /b 0

:error
echo.
echo An error occurred. Read the message above.
pause
exit /b 1
