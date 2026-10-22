<#
.SYNOPSIS
    Registers stanradar as a Windows scheduled task that polls every 10 minutes.

.DESCRIPTION
    A stopgap for when you have no server and GitHub's scheduler is unreliable. It needs
    no card, no token and no third party - but it only polls while this machine is awake,
    so it will miss anything posted overnight if the PC sleeps. That is a real limitation,
    not a detail: the listings worth catching are often posted early.

    Running this twice is safe; it replaces the existing task rather than adding a second.

.PARAMETER IntervalMinutes
    How often to poll. Default 10.

.PARAMETER Remove
    Unregister the task and exit.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy\register-windows-task.ps1
    powershell -ExecutionPolicy Bypass -File deploy\register-windows-task.ps1 -Remove
#>
param(
    [int]$IntervalMinutes = 10,
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$TaskName = 'stanradar'

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed scheduled task '$TaskName'."
    } else {
        Write-Host "No scheduled task named '$TaskName'."
    }
    return
}

# Resolve the repo from this script's location, so it works wherever the repo lives.
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python   = Join-Path $RepoRoot '.venv\Scripts\python.exe'

if (-not (Test-Path $Python)) {
    throw "No virtualenv at $Python. Create it first: python -m venv .venv; .venv\Scripts\pip install -e ."
}
if (-not (Test-Path (Join-Path $RepoRoot 'config.yaml'))) {
    throw "No config.yaml in $RepoRoot. Copy config.example.yaml to config.yaml first."
}
if (-not (Test-Path (Join-Path $RepoRoot '.env'))) {
    Write-Warning "No .env in $RepoRoot - without DISCORD_WEBHOOK_URL the task will run but cannot alert."
}

# -u keeps output unbuffered so the log file stays current rather than lagging a buffer.
$Action = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument '-u -m stanradar watch --once' `
    -WorkingDirectory $RepoRoot

# RepetitionDuration of [TimeSpan]::MaxValue means "repeat forever" to Task Scheduler.
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration ([TimeSpan]::MaxValue)

# StartWhenAvailable catches up after the machine was asleep or off at the trigger time.
# The execution limit stops a hung cycle from blocking every later one.
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Settings $Settings -Description 'Poll Belgrade rental portals and alert on Discord' `
    -Force | Out-Null

Write-Host "Registered '$TaskName': every $IntervalMinutes minutes, from $RepoRoot"
Write-Host ""
Write-Host "  Check it:   Get-ScheduledTask -TaskName $TaskName"
Write-Host "  Run now:    Start-ScheduledTask -TaskName $TaskName"
Write-Host "  Last result: (Get-ScheduledTaskInfo -TaskName $TaskName).LastTaskResult   # 0 = OK"
Write-Host "  Remove:     powershell -ExecutionPolicy Bypass -File deploy\register-windows-task.ps1 -Remove"
Write-Host ""
Write-Host "NOTE: this only polls while the machine is awake. If it sleeps overnight you"
Write-Host "will miss listings posted overnight - which is when a lot of them appear."
