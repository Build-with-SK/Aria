# ============================================================================
# push_data_to_mini.ps1 — laptop → Mac mini fresh-signal sync.
# Appended to the TradingIntelligenceSystem scheduled task so the mini's desk
# always debates today's signals even though the heavy pipeline runs here.
#
# One-time setup on the mini: System Settings → Sharing → Remote Login (SSH) ON.
# One-time on the laptop: set MINI_HOST env var (e.g. "aria@192.168.1.42"),
#   and `ssh-copy-id` / add your public key so scp needs no password.
# ============================================================================
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
$Mini = $env:MINI_HOST
if (-not $Mini) { Write-Output "MINI_HOST not set — skipping mini sync"; exit 0 }
$Dest = "${Mini}:~/aria/data/"

$files = @(
  "data/signals.json",
  "data/macro_data.json",
  "data/ml_predictions.json",
  "data/daily_report.json"
)
foreach ($f in $files) {
  $p = Join-Path $Root $f
  if (Test-Path $p) {
    scp -q -o StrictHostKeyChecking=accept-new $p "${Dest}$(Split-Path $f -Leaf)"
    if ($?) { Write-Output "pushed $f" } else { Write-Output "FAILED $f" }
  }
}
# political dir (recursive)
$pol = Join-Path $Root "data/political"
if (Test-Path $pol) {
  scp -q -r -o StrictHostKeyChecking=accept-new $pol "${Mini}:~/aria/data/"
  Write-Output "pushed data/political/"
}
Write-Output "mini sync done $(Get-Date -Format o)"
