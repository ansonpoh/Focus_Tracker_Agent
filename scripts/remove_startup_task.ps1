$ErrorActionPreference = "Stop"

$taskName = "FocusTrackerAgent"

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Scheduled task '$taskName' removed."
} else {
    Write-Host "Scheduled task '$taskName' was not found."
}
