@echo off
title ARIA Trading Intelligence System

REM %~dp0 is the folder this script lives in, with a trailing backslash.
REM Everything below is relative to it, so the launcher works wherever the repo
REM is cloned and for whoever clones it. It used to hardcode one machine's
REM absolute paths, which meant it worked for exactly one person on one PC.
set "ARIA_HOME=%~dp0"
cd /d "%ARIA_HOME%"

REM API keys are loaded from .env by backend/main.py (load_dotenv).
REM No secrets live in this file. Manage keys in .env only.

if not exist "%ARIA_HOME%venv\Scripts\python.exe" (
  echo.
  echo  ERROR: no virtualenv found at venv\Scripts\python.exe
  echo  Create one first:
  echo      python -m venv venv
  echo      venv\Scripts\pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)

echo.
echo  =========================================
echo   ARIA TRADING INTELLIGENCE SYSTEM
echo  =========================================
echo.
echo  [1/2] Starting Backend on port 8000...

start "ARIA Backend" cmd /k "cd /d "%ARIA_HOME%" && "%ARIA_HOME%venv\Scripts\python.exe" -m uvicorn backend.main:app --port 8000"

timeout /t 5 /nobreak >nul

echo  [2/2] Starting Frontend on port 3000...

start "ARIA Frontend" cmd /k "cd /d "%ARIA_HOME%frontend" && npm run dev"

timeout /t 6 /nobreak >nul

start "" "http://localhost:3000/"

echo.
echo  =========================================
echo   ARIA IS RUNNING
echo.
echo   Dashboard:   http://localhost:3000/
echo   ARIA Chat:   http://localhost:3000/chat
echo   The Desk:    http://localhost:3000/desk
echo  =========================================
echo.
echo  Keep the Backend + Frontend windows open.
echo  Press any key to close this window only.
pause >nul
