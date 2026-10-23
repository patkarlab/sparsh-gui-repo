@echo off
:: SPARSH Run Manager — Windows dev launcher
:: Double-click this to run without installing.
:: Requires Python 3.10+ and: pip install customtkinter watchdog

title SPARSH Run Manager

where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo Python not found in PATH.
    echo Install from https://python.org and re-run this script.
    pause
    exit /b 1
)

python -c "import customtkinter" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo Installing dependencies...
    python -m pip install customtkinter watchdog
)

cd /d "%~dp0"
python main.py
