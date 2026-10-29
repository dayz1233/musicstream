@echo off
title MusicStream
echo ========================================
echo   MusicStream Desktop
echo ========================================
echo.
echo Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo Python not found. Install Python 3.9+ from python.org
    pause
    exit /b 1
)
echo Installing dependencies (including drag-and-drop support)...
pip install -r requirements.txt tkinterdnd2 --quiet
echo.
echo Launching desktop window...
python desktop_app.py
pause
