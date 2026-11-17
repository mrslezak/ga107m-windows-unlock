# Temporarily suspend BitLocker protectors for a limited reboot window.
# Does NOT decrypt the drive.
#
# Usage (elevated PowerShell):
#   .\bitlocker-temporary-suspend.ps1
#   .\bitlocker-temporary-suspend.ps1 -Drive C: -RebootCount 3
#   .\bitlocker-temporary-suspend.ps1 -Drive C: -RebootCount 1 -SkipEnable
#
# Default behavior:
# 1) Re-enable protectors first (idempotent)
# 2) Disable protectors with a finite reboot count
param(
  [ValidatePattern('^[A-Za-z]:$')]
  [string]$Drive = 'C:',

  [ValidateRange(1, 15)]
  [int]$RebootCount = 3,

  [switch]$SkipEnable
)

$ErrorActionPreference = 'Stop'

function Assert-Admin {
  $principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
  if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from an elevated PowerShell window.'
  }
}

function Invoke-ManageBde([string[]]$Args) {
  & manage-bde @Args
  if ($LASTEXITCODE -ne 0) {
    throw "manage-bde failed with exit code $LASTEXITCODE for args: $($Args -join ' ')"
  }
}

Assert-Admin

if (-not $SkipEnable) {
  Invoke-ManageBde -Args @('-protectors', '-enable', $Drive)
}

Invoke-ManageBde -Args @('-protectors', '-disable', $Drive, '-RebootCount', "$RebootCount")

"BitLocker protectors suspended on $Drive for the next $RebootCount reboot(s)."
"No decryption was performed."
"To re-enable immediately: manage-bde -protectors -enable $Drive"
