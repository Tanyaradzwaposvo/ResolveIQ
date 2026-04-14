@echo off
title ResolveIQ — IT Help Desk Agent

:: ── Set your Anthropic API key here ──────────────────────────────────────────
set ANTHROPIC_API_KEY=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

:: ─────────────────────────────────────────────────────────────────────────────

cd /d "%~dp0"

echo.
echo  Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python not found. Please install Python 3.10+ from https://python.org
    pause
    exit /b 1
)

echo  Installing dependencies...
pip install -r requirements.txt --quiet

echo.
echo  Starting ResolveIQ...
echo  Open your browser: http://localhost:5000
echo  Press Ctrl+C to stop.
echo.

python app.py

pause
