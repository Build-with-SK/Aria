# ─────────────────────────────────────────────────────────────────────────────
# ARIA Trading Intelligence System — One-Click Startup
# Run this file: Right-click → Run with PowerShell
# ─────────────────────────────────────────────────────────────────────────────

$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
$VENV = "$ROOT\venv\Scripts"
$FRONTEND = "$ROOT\frontend"

Write-Host ""
Write-Host "  ▶ ARIA TRADING INTELLIGENCE SYSTEM" -ForegroundColor Yellow
Write-Host "  Starting all services..." -ForegroundColor Gray
Write-Host ""

# Load .env
if (Test-Path "$ROOT\.env") {
    Get-Content "$ROOT\.env" | ForEach-Object {
        if ($_ -match '^\s*([^#][^=]+)=(.+)$') {
            [System.Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), 'Process')
        }
    }
    Write-Host "  ✓ Loaded .env" -ForegroundColor Green
} else {
    Write-Host "  ⚠ No .env file found. Copy .env.example to .env and add your API keys." -ForegroundColor Red
}

# Start Backend API
Write-Host "  ▶ Starting Backend API on port 8000..." -ForegroundColor Cyan
$backend = Start-Process -FilePath "$VENV\uvicorn.exe" `
    -ArgumentList "backend.main:app", "--reload", "--port", "8000" `
    -WorkingDirectory $ROOT `
    -PassThru -WindowStyle Normal
Write-Host "  ✓ Backend started (PID $($backend.Id))" -ForegroundColor Green

Start-Sleep -Seconds 2

# Start Frontend
Write-Host "  ▶ Starting Frontend on port 3000..." -ForegroundColor Cyan
$frontend = Start-Process -FilePath "cmd.exe" `
    -ArgumentList "/c", "cd `"$FRONTEND`" && npm run dev" `
    -WorkingDirectory $FRONTEND `
    -PassThru -WindowStyle Normal
Write-Host "  ✓ Frontend started (PID $($frontend.Id))" -ForegroundColor Green

Start-Sleep -Seconds 3

Write-Host ""
Write-Host "  ════════════════════════════════════════" -ForegroundColor Yellow
Write-Host "  ✅ ARIA IS RUNNING" -ForegroundColor Green
Write-Host ""
Write-Host "  Dashboard:  http://localhost:3000" -ForegroundColor White
Write-Host "  API Docs:   http://localhost:8000/docs" -ForegroundColor White
Write-Host "  Execution:  http://localhost:3000/execute" -ForegroundColor White
Write-Host "  ════════════════════════════════════════" -ForegroundColor Yellow
Write-Host ""

# Open browser
Start-Process "http://localhost:3000/execute"

Write-Host "  Press any key to STOP all services..." -ForegroundColor Gray
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")

# Cleanup
Stop-Process -Id $backend.Id  -Force -ErrorAction SilentlyContinue
Stop-Process -Id $frontend.Id -Force -ErrorAction SilentlyContinue
Write-Host "  ✓ All services stopped." -ForegroundColor Gray
