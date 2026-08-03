@echo off
REM ============================================================
REM  AUTORUN_ARIA — starts ARIA (backend 8000 + dashboard 3000)
REM  at login. ATLAS is launched on demand (LAUNCH_ATLAS.bat).
REM  Frees stale ports first so a leftover process never blocks.
REM
REM  Paths are derived from %~dp0 (this script's own folder), so the
REM  launcher follows the repo instead of naming one machine's user.
REM ============================================================
title ARIA Autostart
set "ARIA_HOME=%~dp0"

REM Ensure Node is on PATH (installer adds it system-wide, but a session
REM started before install / at early login may not see it yet).
set PATH=%ProgramFiles%\nodejs;%PATH%

for %%P in (8000 3000) do (
  for /f "tokens=5" %%A in ('netstat -aon ^| findstr ":%%P " ^| findstr LISTENING') do (
    taskkill /F /PID %%A >nul 2>&1
  )
)
timeout /t 2 /nobreak >nul

REM Clear Vite's optimized-dep cache — a partial/stale cache after an
REM autostart restart causes a blank (white-screen) dashboard.
rmdir /s /q "%ARIA_HOME%frontend\node_modules\.vite" 2>nul

start "ARIA Backend" /min cmd /c "cd /d "%ARIA_HOME%" && venv\Scripts\python.exe -m uvicorn backend.main:app --port 8000"
start "ARIA Frontend" /min cmd /c "cd /d "%ARIA_HOME%frontend" && npm.cmd run dev"
