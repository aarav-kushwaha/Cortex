@echo off
setlocal

set "APP_DIR=%~dp0"
set "VENV_PY=%APP_DIR%.venv-exe\Scripts\python.exe"
set "RUNTIME_PY="

if exist "%VENV_PY%" (
    set "RUNTIME_PY=%VENV_PY%"
)

if not defined RUNTIME_PY (
    where python >nul 2>nul
    if not errorlevel 1 set "RUNTIME_PY=python"
)

if not defined RUNTIME_PY (
    echo No Python installation was found.
    echo Install Python 3.10+ and run: pip install -r requirements.txt pyinstaller
    pause
    exit /b 1
)

echo Building Cortex EXE...
"%RUNTIME_PY%" "%APP_DIR%build_exe.py"
if errorlevel 1 (
    echo.
    echo EXE build failed. Make sure PyInstaller is installed: pip install pyinstaller
    pause
    exit /b 1
)

echo.
echo Build completed:
echo   %APP_DIR%dist\Cortex\Cortex.exe
pause
