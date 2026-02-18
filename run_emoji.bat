@echo off
title Emoji Password Study

echo ================================
echo  Emoji Password Study Launcher
echo ================================
echo.

REM check if Python is installed
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo Python is not installed.
    echo Please install Python 3.10+ first.
    pause
    exit
)

REM check if virtual environment exists, if not create it
if not exist .venv (
    echo Creating virtual environment...
    python -m venv .venv
)

REM activate virtual environment
call .venv\Scripts\activate

REM install dependencies
pip install -r requirements.txt

echo.
echo Starting server...
echo.

REM open browser to the app
start http://127.0.0.1:5000
python app.py

pause
