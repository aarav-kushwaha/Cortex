@echo off
cd /d "%~dp0"
title Cortex
python cortex_app.py
if errorlevel 1 py cortex_app.py
if errorlevel 1 (
  echo.
  echo Could not start. Make sure Python is installed and run: pip install -r requirements.txt
  pause
)
