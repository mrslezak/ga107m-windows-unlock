# Installs/removes a SYSTEM startup task that runs hold_gpu_cuda.py to keep the dGPU awake (AC power only).
# Needs an elevated PowerShell because the task runs as SYSTEM.
#
# Usage:  .\setup-hold-task-at-boot.ps1            # install
#         .\setup-hold-task-at-boot.ps1 -Remove    # remove
param(
  [switch]$Remove,
  [string]$TaskName = 'GA107M GPU Hold',
  [string]$PythonExe = '',
  [string]$HoldScript = ''
)
$ErrorActionPreference = 'Stop'

$principalCheck = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (!$principalCheck.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw 'Run this from an elevated PowerShell (the task runs as SYSTEM)'
}

if ($Remove) {
  if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    "Removed: $TaskName"
  } else { "No task named '$TaskName'" }
  exit 0
}

if (!$PythonExe) {
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if (!$cmd) { throw 'python not found on PATH; pass -PythonExe explicitly' }
  $PythonExe = $cmd.Source
}
if (!$HoldScript) { $HoldScript = Join-Path $PSScriptRoot 'hold_gpu_cuda.py' }
if (!(Test-Path $PythonExe)) { throw "Python not found: $PythonExe" }
if (!(Test-Path $HoldScript)) { throw "Hold script not found: $HoldScript" }
# SYSTEM cannot run the per-user Microsoft Store python alias.
if ($PythonExe -like '*\WindowsApps\*') { throw 'Store python alias will not run as SYSTEM; pass -PythonExe with a full install path' }

$action = New-ScheduledTaskAction -Execute $PythonExe -Argument "`"$HoldScript`"" -WorkingDirectory (Split-Path $HoldScript)
$trigger = New-ScheduledTaskTrigger -AtStartup
# Default settings already start only on AC and stop on battery; the unlock is pointless at battery power limits.
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
  -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null

"Installed: $TaskName"
"  Runs: $PythonExe `"$HoldScript`""
"Starts at boot on AC power; stops if the PC switches to battery."
"Start it now with:  Start-ScheduledTask -TaskName '$TaskName'"
"Remove later with:  .\setup-hold-task-at-boot.ps1 -Remove"
