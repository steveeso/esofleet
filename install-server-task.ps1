$proj = $PSScriptRoot
$py = Join-Path $proj "venv\Scripts\python.exe"
$log = Join-Path $proj "server.out.log"
$cmd = "Set-Location '$proj'; & '$py' run.py *>> '$log'"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -Command `"$cmd`"" `
    -WorkingDirectory $proj

# At logon, plus every 5 minutes as a watchdog. IgnoreNew means a run is a
# no-op while the server is already up, and relaunches it if it died.
$logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)

$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName "EsoFleet Server" -Action $action `
    -Trigger @($logon, $repeat) -Settings $settings -Principal $principal -Force |
    Select-Object TaskName, State

Start-ScheduledTask -TaskName "EsoFleet Server"
Write-Host "Registered and started. Remove later with: Unregister-ScheduledTask -TaskName 'EsoFleet Server' -Confirm:`$false"
