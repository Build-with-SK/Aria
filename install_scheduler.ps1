
$Action  = New-ScheduledTaskAction -Execute 'C:\Program Files\Python311\python.exe' -Argument '"C:\Users\sound\documents\trading-intelligence-system\main.py" ' -WorkingDirectory 'C:\Users\sound\documents\trading-intelligence-system'
$Trigger = New-ScheduledTaskTrigger -Daily -At 07:30
$Settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 2) -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName 'TradingIntelligenceSystem' -Action $Action -Trigger $Trigger -Settings $Settings -RunLevel Highest -Force
Write-Host "Task 'TradingIntelligenceSystem' created. Will run daily at 07:30."
