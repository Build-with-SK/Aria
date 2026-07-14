@echo off
title ARIA Trading Intelligence System

cd /d "C:\Users\sound\Documents\trading-intelligence-system"

REM API keys are loaded from .env by backend/main.py (load_dotenv).
REM No secrets live in this file anymore. Manage keys in .env only.

echo.
echo  =========================================
echo   ARIA TRADING INTELLIGENCE SYSTEM
echo  =========================================
echo.
echo  [1/2] Starting Backend on port 8000...

start "ARIA Backend" cmd /k "cd /d "C:\Users\sound\Documents\trading-intelligence-system" && C:\Users\sound\Documents\trading-intelligence-system\venv\Scripts\python.exe -m uvicorn backend.main:app --port 8000"

timeout /t 5 /nobreak >nul

echo  [2/2] Starting Frontend on port 3000...

start "ARIA Frontend" cmd /k "cd /d "C:\Users\sound\Documents\trading-intelligence-system\frontend" && npm run dev"

timeout /t 6 /nobreak >nul

start "" "http://localhost:3000/chat"
timeout /t 1 /nobreak >nul
start "" "http://localhost:3000/execute"

echo.
echo  =========================================
echo   ARIA IS RUNNING
echo.
echo   ARIA Chat:   http://localhost:3000/chat
echo   Execution:   http://localhost:3000/execute
echo   Dashboard:   http://localhost:3000/
echo  =========================================
echo.
echo  Keep the Backend + Frontend windows open.
echo  Press any key to close this window only.
pause >nul
