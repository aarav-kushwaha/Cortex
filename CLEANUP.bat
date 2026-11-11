@echo off
setlocal enableextensions
cd /d "%~dp0"
title Cortex - move old files to recycle

echo ============================================================
echo   CORTEX CLEANUP
echo ============================================================
echo.
echo This moves everything you no longer need into a single folder
echo you can delete (or send to the recycle bin) yourself. Nothing
echo is permanently removed by this script.
echo.
echo It will move:
echo   ..\stock_scanner\                 ^(the whole old project^)
echo   cortex-release\app.py             ^(old UI stub^)
echo   cortex-release\RUN_OLD_APP.bat    ^(old launcher^)
echo   cortex-release\build\             ^(PyInstaller temp^)
echo   cortex-release\dist\              ^(previous EXE build - if any^)
echo   cortex-release\__pycache__\
echo   cortex-release\.aether_cache\     ^(downloaded market data cache^)
echo.
echo Everything is moved into:
echo   ..\_to_delete_%%DATE%%\
echo.
set "ANS="
set /p ANS=Type YES and press Enter to continue: 
if /I not "%ANS%"=="YES" (
  echo.
  echo Cancelled. Nothing was moved.
  pause
  exit /b 0
)

set "STAMP=%DATE:/=-%_%TIME::=-%"
set "STAMP=%STAMP: =0%"
set "STAMP=%STAMP:,=.%"
set "DEST=..\_to_delete_%STAMP%"
mkdir "%DEST%" 2>nul

if exist "..\stock_scanner" (
  echo Moving old stock_scanner...
  move /Y "..\stock_scanner" "%DEST%\stock_scanner" >nul
)

for %%N in (app.py RUN_OLD_APP.bat) do (
  if exist "%%N" (
    echo Moving %%N...
    move /Y "%%N" "%DEST%\%%N" >nul
  )
)

for %%D in (build dist __pycache__ .aether_cache) do (
  if exist "%%D" (
    echo Moving %%D...
    move /Y "%%D" "%DEST%\%%D" >nul
  )
)

if exist "*.spec" (
  echo Moving .spec files...
  move /Y "*.spec" "%DEST%\" >nul
)

echo.
echo ============================================================
echo   Done.
echo   Everything moved into:
echo     %CD%\%DEST%
echo   When you're sure nothing's needed, delete that folder
echo   (right-click -^> Delete, or drag to the Recycle Bin).
echo ============================================================
pause
