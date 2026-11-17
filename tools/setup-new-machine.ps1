param(
  [Parameter(Mandatory=$true)][string]$ReportPath,
  [Parameter(Mandatory=$true)][string]$AssetsDir,
  [string]$Python = 'python',
  [ValidateSet('entryLatchedSs1Preserve','entryLatchedSs1Full')][string]$StockProfile = 'entryLatchedSs1Full',
  [string]$StageRoot = '',
  [ValidatePattern('^[A-Za-z]$')][string]$UsbDriveLetter = '',
  [switch]$UsbFormat,
  [switch]$UsbClearExisting,
  [string]$UsbVolumeLabel = 'GPU-UNLOCK',
  [string]$UsbConfirmErase = ''
)
$ErrorActionPreference = 'Stop'

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $StageRoot) {
  $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
  $StageRoot = Join-Path $repo "staging/new-machine-$stamp"
}
$report = (Resolve-Path $ReportPath).Path
$assets = (Resolve-Path $AssetsDir).Path

$profilesDir = Join-Path $PSScriptRoot 'profiles'
New-Item -ItemType Directory -Path $profilesDir -Force | Out-Null
$profilePath = Join-Path $profilesDir ('profile-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.json')

Push-Location -LiteralPath $repo
try {
  & $Python (Join-Path $PSScriptRoot 'make_profile.py') --report $report --output $profilePath
  if ($LASTEXITCODE -ne 0) { throw 'make_profile.py failed' }

  & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repo 'unlock/build.ps1') -Probe -Profile $profilePath
  if ($LASTEXITCODE -ne 0) { throw 'Probe build failed' }

  & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repo 'unlock/build.ps1') -ExperimentalUnlock -StockProfile $StockProfile -Profile $profilePath
  if ($LASTEXITCODE -ne 0) { throw 'Experimental build failed' }

  $efiSrc = Join-Path $repo "unlock/build/experimental/$StockProfile/GA107M-EXPERIMENTAL-$StockProfile.EFI"
  if (!(Test-Path -LiteralPath $efiSrc)) { throw "Expected EFI not found: $efiSrc" }

  if (Test-Path -LiteralPath $StageRoot) { throw "StageRoot already exists: $StageRoot" }
  $bootDir = Join-Path $StageRoot 'EFI/BOOT'
  $assetOut = Join-Path $StageRoot 'RTX3090-UNLOCK/ASSETS'
  $windowsOut = Join-Path $StageRoot 'WINDOWS-AFTER-BOOT'
  New-Item -ItemType Directory -Path $bootDir,$assetOut,$windowsOut -Force | Out-Null
  Copy-Item -LiteralPath $efiSrc -Destination (Join-Path $bootDir 'BOOTX64.EFI') -Force

  $spec = Get-Content -LiteralPath (Join-Path $repo 'unlock/assets.json') -Raw | ConvertFrom-Json
  foreach ($entry in $spec) {
    $src = Join-Path $assets $entry.Name
    if (!(Test-Path -LiteralPath $src)) { throw "Missing asset: $src" }
    $size = (Get-Item -LiteralPath $src).Length
    if ($size -ne [int64]$entry.Bytes) { throw "Asset size mismatch for $($entry.Name): expected $($entry.Bytes), got $size" }
    $hash = (Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -ne $entry.Sha256.ToLowerInvariant()) { throw "Asset hash mismatch for $($entry.Name)" }
    Copy-Item -LiteralPath $src -Destination (Join-Path $assetOut $entry.Name) -Force
  }

  Copy-Item -LiteralPath $profilePath -Destination (Join-Path $windowsOut 'machine-profile.json') -Force
  Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'hold_gpu_cuda.py') -Destination (Join-Path $windowsOut 'hold_gpu_cuda.py') -Force
  Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'setup-hold-task-at-boot.ps1') -Destination (Join-Path $windowsOut 'setup-hold-task-at-boot.ps1') -Force
  Copy-Item -LiteralPath (Join-Path $repo "unlock/build/probe/build-manifest.json") -Destination (Join-Path $windowsOut 'probe-build-manifest.json') -Force
  Copy-Item -LiteralPath (Join-Path $repo "unlock/build/experimental/$StockProfile/build-manifest.json") -Destination (Join-Path $windowsOut 'experimental-build-manifest.json') -Force

  $postBoot = @"
AFTER BOOTING FROM THIS USB

At the UEFI menu:
1. Run the unlock payload and authorize with U only when prompted.
2. Immediately boot Windows Boot Manager from firmware (no reboot/reset in between).

After Windows login (elevated PowerShell):
1. Set-Location .\WINDOWS-AFTER-BOOT
2. powershell -NoProfile -ExecutionPolicy Bypass -File .\setup-hold-task-at-boot.ps1
3. Start-ScheduledTask -TaskName 'GA107M GPU Hold'

Verification:
1. In Task Scheduler, confirm 'GA107M GPU Hold' is Running.
2. Optional: run benchmark scripts after keep-alive is active.
"@
  $postBoot | Set-Content -LiteralPath (Join-Path $windowsOut 'POST-BOOT-STEPS.txt') -Encoding UTF8

  $usbSummary = 'USB not written by this command (stage only).'
  if ($UsbDriveLetter) {
    $usbScript = Join-Path $PSScriptRoot 'write-usb-fat32.ps1'
    if (!(Test-Path -LiteralPath $usbScript)) { throw "USB writer script missing: $usbScript" }
    $usbArgs = @(
      '-NoProfile','-ExecutionPolicy','Bypass','-File',$usbScript,
      '-DriveLetter',$UsbDriveLetter,
      '-SourceDir',$StageRoot
    )
    if ($UsbFormat) { $usbArgs += '-Format' }
    if ($UsbClearExisting) { $usbArgs += '-ClearExisting' }
    if ($UsbVolumeLabel) { $usbArgs += @('-VolumeLabel',$UsbVolumeLabel) }
    if ($UsbConfirmErase) { $usbArgs += @('-ConfirmErase',$UsbConfirmErase) }
    & powershell @usbArgs
    if ($LASTEXITCODE -ne 0) { throw 'USB write step failed' }
    $usbSummary = "USB write complete: $($UsbDriveLetter.ToUpperInvariant()):"
  }

  @"
New machine package generated.

Manual steps still required:
1. In BIOS/UEFI, allow USB boot and disable Secure Boot where required.
2. Boot this USB and run unlock; press U to authorize when prompted.
3. Boot directly to Windows Boot Manager without reset.
4. Open WINDOWS-AFTER-BOOT\POST-BOOT-STEPS.txt and run the listed commands.

Package paths:
- EFI image: EFI\BOOT\BOOTX64.EFI
- Firmware assets: RTX3090-UNLOCK\ASSETS\
- Post-boot helper files: WINDOWS-AFTER-BOOT\

$usbSummary
"@ | Set-Content -LiteralPath (Join-Path $StageRoot 'README-FIRST.txt') -Encoding UTF8

  "Staged package: $StageRoot"
  $usbSummary
  "Post-boot steps: $windowsOut\POST-BOOT-STEPS.txt"
} finally {
  Pop-Location
}
