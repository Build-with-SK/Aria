# scripts/install_boot_task.ps1
# ------------------------------------------------------------------
# Start ARIA's backend when you log in, and keep it started.
#
# WHY THIS MATTERS MORE THAN IT LOOKS
# Everything that accumulates evidence - the nightly PREDICT leg, the RESOLVE
# leg that moves the resolved-call counter, the desk's management tick - runs
# on a scheduler INSIDE the backend process. No process, no scheduler, no
# track record, however long you wait. This file is the difference between a
# system that is learning and one that only would if it were running.
#
# Run once, from an ordinary PowerShell (no admin needed):
#     powershell -ExecutionPolicy Bypass -File scripts\install_boot_task.ps1
#
# Remove it with:
#     Unregister-ScheduledTask -TaskName 'ARIA Backend' -Confirm:$false
# ------------------------------------------------------------------

$ErrorActionPreference = 'Stop'

$TaskName = 'ARIA Backend'
$Root     = Split-Path -Parent $PSScriptRoot
$Python   = Join-Path $Root 'venv\Scripts\pythonw.exe'   # pythonw = no console window
$Fallback = Join-Path $Root 'venv\Scripts\python.exe'

if (-not (Test-Path $Python)) {
    if (-not (Test-Path $Fallback)) {
        throw "No interpreter found at $Python or $Fallback. Create the venv first."
    }
    $Python = $Fallback
}

Write-Host "root:   $Root"
Write-Host "python: $Python"

# scripts\serve.py rather than `-m uvicorn`, and not a .bat.
#
#   * A .bat means Task Scheduler starts cmd.exe, which leaves an orphan shell
#     when the task stops - two backends then fight over port 8000.
#   * `-m uvicorn` under pythonw.exe exits instantly with code 1 and no message,
#     because pythonw has no stderr for uvicorn's log handler to write to. That
#     is exactly what happened the first time this was installed.
#
# serve.py opens data\logs\backend.log and redirects both streams into it
# before importing uvicorn, so the service is both silent on screen and
# readable after the fact.
$Action = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument 'scripts\serve.py' `
    -WorkingDirectory $Root

# At logon rather than at boot: the app reads .env and the Obsidian vault from
# the user profile, and at boot that profile may not be mounted yet.
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# RestartCount/Interval is the "keep it started" half. A crash at 03:00 should
# cost minutes, not the night's data.
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 2) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)      # never kill a long-running service

$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "replacing the existing '$TaskName' task"
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Settings $Settings -Principal $Principal `
    -Description 'Starts the ARIA backend (FastAPI + the research scheduler) at logon.' | Out-Null

Write-Host ""
Write-Host "registered '$TaskName' - starts at logon, restarts up to 5 times on failure."
Write-Host "starting it now so you do not have to log out..."
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 12

try {
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 10
    Write-Host "backend is up: $($health | ConvertTo-Json -Compress)"
} catch {
    Write-Warning "backend did not answer /health yet. Check with:"
    Write-Warning "  Get-ScheduledTaskInfo -TaskName '$TaskName'"
}

Write-Host ""
Write-Host "Verify the research loop is actually turning (not just the process):"
Write-Host "  curl http://127.0.0.1:8000/api/v5/loop-health"
