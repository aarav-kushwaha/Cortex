@echo off
setlocal

set "APP_DIR=%~dp0"
set "EXE_PATH=%APP_DIR%dist\Cortex\Cortex.exe"

if exist "%EXE_PATH%" (
    start "" "%EXE_PATH%"
    exit /b 0
)

echo Cortex.exe was not found.
echo Expected path:
echo   %EXE_PATH%
echo.
echo Do not run the temporary EXE from the build folder.
echo Always launch the final app from the dist folder or rerun BUILD_EXE.bat.
echo.
echo Use BUILD_EXE.bat to create the Windows application bundle first.
pause
