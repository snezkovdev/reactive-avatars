@echo off
setlocal
cd /d "%~dp0"
title Build Reactive Avatars 2

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
    echo Install Python 3.11 or newer from:
    echo https://www.python.org/downloads/windows/
    echo Enable "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating the build environment...
    %PY_CMD% -m venv ".venv"
    if errorlevel 1 goto error
)

echo Installing build components...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r "requirements.txt" "pyinstaller>=6.11,<7"
if errorlevel 1 goto error

echo.
echo Building the portable Windows application...
".venv\Scripts\python.exe" -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --windowed ^
    --onedir ^
    --name "ReactiveAvatars2" ^
    --collect-all "imageio_ffmpeg" ^
    "main.py"
if errorlevel 1 goto error

if not exist "dist\ReactiveAvatars2\input" mkdir "dist\ReactiveAvatars2\input"
if not exist "dist\ReactiveAvatars2\output" mkdir "dist\ReactiveAvatars2\output"
copy /y "README.txt" "dist\ReactiveAvatars2\README.txt" >nul
copy /y "README.md" "dist\ReactiveAvatars2\README.md" >nul
copy /y "LICENSE" "dist\ReactiveAvatars2\LICENSE" >nul
copy /y "input\README.txt" "dist\ReactiveAvatars2\input\README.txt" >nul

echo.
echo BUILD COMPLETE
echo Open: dist\ReactiveAvatars2
echo Run:  ReactiveAvatars2.exe
echo Copy the whole ReactiveAvatars2 folder to another Windows PC.
echo Python is not required on that PC.
echo.
pause
exit /b 0

:error
echo.
echo The build failed. Read the message above.
pause
exit /b 1
