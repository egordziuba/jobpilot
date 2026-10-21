# Регистрирует задачу в Планировщике Windows: каждый день 08:15 и 15:15, догоняет пропуски
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$bat = Join-Path $root "run.bat"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$bat`" task" -WorkingDirectory $root
$triggers = @(
    (New-ScheduledTaskTrigger -Daily -At 08:15),
    (New-ScheduledTaskTrigger -Daily -At 15:15)
)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "JobPilot Auto" -Action $action -Trigger $triggers -Settings $settings `
    -Description "Поиск вакансий Werkstudent и заявки (JobPilot Auto)" -Force | Out-Null
Write-Host "Задача 'JobPilot Auto' создана: 08:15 и 15:15, пропущенные запуски догоняются при включении ПК." -ForegroundColor Green
Write-Host "Отключить: Планировщик заданий -> JobPilot Auto -> Отключить."
