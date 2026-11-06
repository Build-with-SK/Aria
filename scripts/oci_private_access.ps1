param(
    [Parameter(Mandatory=$true)][string]$Server,
    [string]$KeyPath = "$env:USERPROFILE/.ssh/aria-sentinel-oci.key",
    [string]$PrivateRoot = (Join-Path $env:LOCALAPPDATA 'AriaSentinelDeploy/access')
)
$ErrorActionPreference = 'Stop'
$privateRoot = $PrivateRoot
New-Item -ItemType Directory -Force -Path $privateRoot | Out-Null
$owner = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls.exe $privateRoot /inheritance:r /grant:r "${owner}:(OI)(CI)F" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not restrict access directory' }
@{server=$Server; key=(Resolve-Path -LiteralPath $KeyPath).Path} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $privateRoot 'connection.json') -Encoding UTF8
$tunnelScript = @'
$ErrorActionPreference = 'Stop'
$config = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'connection.json') | ConvertFrom-Json
$ssh = Join-Path $env:WINDIR 'System32/OpenSSH/ssh.exe'
# Windows PowerShell treats native stderr as an ErrorRecord. A normal SSH
# disconnect must reach the reconnect loop rather than terminate this script.
$ErrorActionPreference = 'Continue'
while ($true) {
    & $ssh -N -T -i $config.key -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o ExitOnForwardFailure=yes -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 127.0.0.1:18000:127.0.0.1:8000 -L 127.0.0.1:18300:127.0.0.1:8300 -L 127.0.0.1:4174:127.0.0.1:4174 "ubuntu@$($config.server)" 2>> (Join-Path $PSScriptRoot 'tunnel.log')
    Start-Sleep -Seconds 15
}
'@
$scriptPath = Join-Path $privateRoot 'tunnel.ps1'
$tunnelScript | Set-Content -LiteralPath $scriptPath -Encoding UTF8
$action = New-ScheduledTaskAction -Execute (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$scriptPath`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $owner
$principal = New-ScheduledTaskPrincipal -UserId $owner -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName 'ARIA Sentinel Cloud Tunnel' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName 'ARIA Sentinel Cloud Tunnel'
$backupScript = @'
$ErrorActionPreference = 'Stop'
$config = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'connection.json') | ConvertFrom-Json
$ssh = Join-Path $env:WINDIR 'System32/OpenSSH/ssh.exe'
$scp = Join-Path $env:WINDIR 'System32/OpenSSH/scp.exe'
$options = @('-i',$config.key,'-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15')
$target = "ubuntu@$($config.server)"
$remotePath = (& $ssh @options $target 'ls -1 ServerBackups/server-backup-*.tar.gz | sort | tail -1').Trim()
$remoteName = Split-Path -Leaf $remotePath
if ($LASTEXITCODE -ne 0 -or $remoteName -notmatch '^server-backup-\d{8}_\d{6}\.tar\.gz$') { throw 'No remote backup available' }
$directory = Join-Path $PSScriptRoot 'backups'
New-Item -ItemType Directory -Force -Path $directory | Out-Null
$destination = Join-Path $directory $remoteName
& $scp @options "${target}:ServerBackups/$remoteName" $destination
if ($LASTEXITCODE -ne 0) { throw 'Backup download failed' }
$digestLine = & $ssh @options $target "sha256sum ServerBackups/$remoteName"
if ($LASTEXITCODE -ne 0) { throw 'Remote checksum unavailable' }
$expected = ($digestLine -split '\s+')[0]
$actual = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash
if ($actual -ne $expected) { throw 'Downloaded backup checksum mismatch' }
"$([DateTime]::UtcNow.ToString('o')) verified $remoteName" | Add-Content -LiteralPath (Join-Path $PSScriptRoot 'backup.log')
Get-ChildItem -LiteralPath $directory -Filter 'server-backup-*.tar.gz' -File |
    Sort-Object Name -Descending | Select-Object -Skip 14 | ForEach-Object {
        if ($_.Name -match '^server-backup-\d{8}_\d{6}\.tar\.gz$') { Remove-Item -LiteralPath $_.FullName }
    }
'@
$backupPath = Join-Path $privateRoot 'backup.ps1'
$backupScript | Set-Content -LiteralPath $backupPath -Encoding UTF8
$backupAction = New-ScheduledTaskAction -Execute (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$backupPath`""
$backupTriggers = @((New-ScheduledTaskTrigger -Daily -At '04:00'), (New-ScheduledTaskTrigger -AtLogOn -User $owner))
Register-ScheduledTask -TaskName 'ARIA Sentinel Cloud Backup' -Action $backupAction -Trigger $backupTriggers -Principal $principal -Settings $settings -Force | Out-Null
Write-Output 'Private tunnel installed for this Windows account; app ports remain loopback-only.'
