@echo off
REM ============================================================
REM  AUTORUN_ARIA — starts ARIA (backend 8000 + dashboard 3000)
REM  at login. ATLAS is launched on demand (LAUNCH_ATLAS.bat).
REM  Frees stale ports first so a leftover process never blocks.
REM ============================================================
title ARIA Autostart
REM Ensure Node is on PATH (installer adds it system-wide, but a session
REM started before install / at early login may not see it yet).
set PATH=C:\Program Files\nodejs;%PATH%

for %%P in (8000 3000) do (
  for /f "tokens=5" %%A in ('netstat -aon ^| findstr ":%%P " ^| findstr LISTENING') do (
    taskkill /F /PID %%A >nul 2>&1
  )
)
timeout /t 2 /nobreak >nul

start "ARIA Backend" /min cmd /c "cd /d C:\Users\sound\Documents\trading-intelligence-system && venv\Scripts\python.exe -m uvicorn backend.main:app --port 8000"
start "ARIA Frontend" /min cmd /c "cd /d C:\Users\sound\Documents\trading-intelligence-system\frontend && C:\PROGRA~1\nodejs\npm.cmd run dev"
