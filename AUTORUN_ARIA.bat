@echo off
REM ============================================================
REM  AUTORUN_ARIA — starts ARIA (backend 8000 + dashboard 3000)
REM  at login. ATLAS is launched on demand (LAUNCH_ATLAS.bat).
REM  Frees stale ports first so a leftover process never blocks —
REM  but leaves a backend that answers /health running (see below).
REM
REM  Paths are derived from %~dp0 (this script's own folder), so the
REM  launcher follows the repo instead of naming one machine's user.
REM ============================================================
title ARIA Autostart
set "ARIA_HOME=%~dp0"

REM Ensure Node is on PATH (installer adds it system-wide, but a session
REM started before install / at early login may not see it yet).
set PATH=%ProgramFiles%\nodejs;%PATH%

REM A HEALTHY BACKEND IS NOT A STALE ONE. The "ARIA Backend" scheduled task
REM (scripts\install_boot_task.ps1) starts the backend at the same login, with
REM its output going to data\logs\backend.log. This script used to kill whatever
REM held port 8000 and start a second, unlogged backend in its place: on
REM 2026-10-08 the task's backend lived two minutes, and when the replacement
REM died on 2026-10-10 it left no trace of why. So: if that task exists, give
REM its backend up to ~100s to answer /health; a backend that answers is left
REM alone. Only a port holder that does NOT answer is killed, as before.
set "ARIA_BACKEND_UP="
schtasks /query /tn "ARIA Backend" >nul 2>&1
if not errorlevel 1 (
  for /l %%i in (1,1,20) do (
    if not defined ARIA_BACKEND_UP (
      curl.exe -f -s -o nul --max-time 2 http://127.0.0.1:8000/health && set "ARIA_BACKEND_UP=1"
      if not defined ARIA_BACKEND_UP ping -n 4 127.0.0.1 >nul
    )
  )
) else (
  curl.exe -f -s -o nul --max-time 2 http://127.0.0.1:8000/health && set "ARIA_BACKEND_UP=1"
)

for /f "tokens=5" %%A in ('netstat -aon ^| findstr ":3000 " ^| findstr LISTENING') do (
  taskkill /F /PID %%A >nul 2>&1
)
if not defined ARIA_BACKEND_UP (
  for /f "tokens=5" %%A in ('netstat -aon ^| findstr ":8000 " ^| findstr LISTENING') do (
    taskkill /F /PID %%A >nul 2>&1
  )
)
timeout /t 2 /nobreak >nul

REM Clear Vite's optimized-dep cache — a partial/stale cache after an
REM autostart restart causes a blank (white-screen) dashboard.
rmdir /s /q "%ARIA_HOME%frontend\node_modules\.vite" 2>nul

if not defined ARIA_BACKEND_UP start "ARIA Backend" /min cmd /c "cd /d "%ARIA_HOME%" && venv\Scripts\python.exe -m uvicorn backend.main:app --port 8000"
start "ARIA Frontend" /min cmd /c "cd /d "%ARIA_HOME%frontend" && npm.cmd run dev"
