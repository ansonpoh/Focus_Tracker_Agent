$ErrorActionPreference = "Stop"

$taskName = "FocusTrackerAgent"
$repoRoot = Split-Path -Parent $PSScriptRoot
$launcherPath = Join-Path $PSScriptRoot "launch_focus_tracker.vbs"
$wscriptPath = Join-Path $env:SystemRoot "System32\wscript.exe"
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

if (-not (Test-Path -LiteralPath $launcherPath)) {
    throw "Launcher script not found: $launcherPath"
}

if (-not (Test-Path -LiteralPath $wscriptPath)) {
    throw "wscript.exe not found: $wscriptPath"
}

$action = New-ScheduledTaskAction -Execute $wscriptPath -Argument "`"$launcherPath`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$trigger.Delay = "PT30S"
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable

$task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings
Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null

Write-Host "Scheduled task '$taskName' installed for $currentUser."
Write-Host "It will start 30 seconds after logon using:"
Write-Host "  $launcherPath"
