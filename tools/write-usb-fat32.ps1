param(
  [Parameter(Mandatory=$true)]
  [ValidatePattern('^[A-Za-z]$')]
  [string]$DriveLetter,

  [Parameter(Mandatory=$true)]
  [string]$SourceDir,

  [switch]$Format,
  [switch]$ClearExisting,
  [string]$VolumeLabel = 'GPU-UNLOCK',

  # Required only when -Format is used. Must match: ERASE <DriveLetter>
  [string]$ConfirmErase = ''
)

$ErrorActionPreference = 'Stop'

function Require-Admin {
  $principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
  if (!$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Formatting requires an elevated PowerShell window.'
  }
}

function Assert-SourceTree([string]$Root) {
  $efi = Join-Path $Root 'EFI/BOOT/BOOTX64.EFI'
  $assets = Join-Path $Root 'RTX3090-UNLOCK/ASSETS'
  if (!(Test-Path -LiteralPath $efi)) {
    throw "Source tree missing EFI payload: $efi"
  }
  if (!(Test-Path -LiteralPath $assets)) {
    throw "Source tree missing assets folder: $assets"
  }
}

$drive = $DriveLetter.ToUpperInvariant()
$src = (Resolve-Path -LiteralPath $SourceDir).Path
Assert-SourceTree -Root $src

$vol = Get-Volume -DriveLetter $drive -ErrorAction SilentlyContinue
if (!$vol) {
  throw "Drive $drive`: not found. Insert USB drive and retry."
}

$part = Get-Partition -DriveLetter $drive -ErrorAction Stop
$disk = Get-Disk -Number $part.DiskNumber -ErrorAction Stop

if ($Format) {
  Require-Admin

  if ($disk.BusType -ne 'USB') {
    throw "Refusing to format non-USB disk (Disk $($disk.Number), BusType=$($disk.BusType))."
  }

  $expected = "ERASE $drive"
  if ($ConfirmErase -ne $expected) {
    throw "Formatting is destructive. Re-run with -ConfirmErase '$expected' to proceed."
  }

  Format-Volume -DriveLetter $drive -FileSystem FAT32 -NewFileSystemLabel $VolumeLabel -Confirm:$false -Force | Out-Null
  Start-Sleep -Seconds 1
}

$destRoot = "$drive`:\"
if (!(Test-Path -LiteralPath $destRoot)) {
  throw "Drive root unavailable: $destRoot"
}

if ($ClearExisting) {
  Get-ChildItem -LiteralPath $destRoot -Force | Where-Object { $_.Name -ne 'System Volume Information' } | Remove-Item -Recurse -Force
}

Copy-Item -LiteralPath (Join-Path $src '*') -Destination $destRoot -Recurse -Force

$bootx64 = Join-Path $destRoot 'EFI/BOOT/BOOTX64.EFI'
if (!(Test-Path -LiteralPath $bootx64)) {
  throw 'Copy verification failed: BOOTX64.EFI missing on target USB.'
}

"USB staging complete on $drive`:"
"  Source: $src"
"  Target: $destRoot"
"  BOOTX64: $bootx64"
