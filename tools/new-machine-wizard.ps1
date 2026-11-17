param(
  [ValidateSet('Step1Probe','Step2Finalize')]
  [string]$Mode = 'Step1Probe',
  [Parameter(Mandatory=$true)][string]$AssetsDir,
  [string]$ReportPath = '',
  [string]$Python = 'python',
  [ValidateSet('entryLatchedSs1Preserve','entryLatchedSs1Full')][string]$StockProfile = 'entryLatchedSs1Full',
  [ValidatePattern('^[A-Za-z]$')][string]$UsbDriveLetter = '',
  [switch]$UsbFormat,
  [switch]$UsbClearExisting,
  [string]$UsbVolumeLabel = 'GPU-UNLOCK',
  [string]$UsbConfirmErase = '',
  [switch]$ConfigureHoldTaskNow,
  [switch]$SkipConfigureHoldPrompt
)

$ErrorActionPreference = 'Stop'

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$assets = (Resolve-Path -LiteralPath $AssetsDir).Path
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'

function Get-MissingAssets([string]$AssetSource,[string]$SpecPath) {
  $spec = Get-Content -LiteralPath $SpecPath -Raw | ConvertFrom-Json
  $missing = @()
  foreach ($entry in $spec) {
    $src = Join-Path $AssetSource $entry.Name
    if (!(Test-Path -LiteralPath $src)) {
      $missing += "missing: $($entry.Name)"
      continue
    }
    $size = (Get-Item -LiteralPath $src).Length
    if ($size -ne [int64]$entry.Bytes) {
      $missing += "size mismatch: $($entry.Name)"
      continue
    }
    $hash = (Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -ne $entry.Sha256.ToLowerInvariant()) {
      $missing += "hash mismatch: $($entry.Name)"
    }
  }
  return $missing
}

function Ensure-AssetsReady([string]$AssetSource,[string]$SpecPath,[string]$PythonExe) {
  $missing = Get-MissingAssets -AssetSource $AssetSource -SpecPath $SpecPath
  if ($missing.Count -eq 0) {
    return
  }

  Write-Host 'Required firmware assets are missing or invalid:' -ForegroundColor Yellow
  foreach ($item in $missing) { Write-Host "  - $item" -ForegroundColor Yellow }

  $answer = Read-Host 'Assets missing, build now? (Y/N)'
  if ($answer -notmatch '^(?i:y|yes)$') {
    throw 'Assets are required. Run tools\\firmware\\build-assets.cmd or choose Y to build now.'
  }

  $buildAssets = Join-Path $PSScriptRoot 'firmware\build-assets.ps1'
  if (!(Test-Path -LiteralPath $buildAssets)) {
    throw "Missing asset builder: $buildAssets"
  }

  & powershell -NoProfile -ExecutionPolicy Bypass -File $buildAssets -Python $PythonExe -OutputDir $AssetSource
  if ($LASTEXITCODE -ne 0) {
    throw 'Automatic asset build failed.'
  }

  $postMissing = Get-MissingAssets -AssetSource $AssetSource -SpecPath $SpecPath
  if ($postMissing.Count -gt 0) {
    throw 'Assets are still missing/invalid after build step.'
  }
}

function Copy-Assets([string]$AssetSource,[string]$AssetDest,[string]$SpecPath) {
  $spec = Get-Content -LiteralPath $SpecPath -Raw | ConvertFrom-Json
  New-Item -ItemType Directory -Path $AssetDest -Force | Out-Null
  foreach ($entry in $spec) {
    $src = Join-Path $AssetSource $entry.Name
    if (!(Test-Path -LiteralPath $src)) { throw "Missing asset: $src" }
    $size = (Get-Item -LiteralPath $src).Length
    if ($size -ne [int64]$entry.Bytes) { throw "Asset size mismatch for $($entry.Name): expected $($entry.Bytes), got $size" }
    $hash = (Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -ne $entry.Sha256.ToLowerInvariant()) { throw "Asset hash mismatch for $($entry.Name)" }
    Copy-Item -LiteralPath $src -Destination (Join-Path $AssetDest $entry.Name) -Force
  }
}

function Write-UsbIfRequested([string]$SourceDir) {
  if (-not $UsbDriveLetter) { return 'USB not written by wizard (stage folder only).' }
  $usbScript = Join-Path $PSScriptRoot 'write-usb-fat32.ps1'
  if (!(Test-Path -LiteralPath $usbScript)) { throw "USB writer script missing: $usbScript" }
  $usbArgs = @(
    '-NoProfile','-ExecutionPolicy','Bypass','-File',$usbScript,
    '-DriveLetter',$UsbDriveLetter,
    '-SourceDir',$SourceDir
  )
  if ($UsbFormat) { $usbArgs += '-Format' }
  if ($UsbClearExisting) { $usbArgs += '-ClearExisting' }
  if ($UsbVolumeLabel) { $usbArgs += @('-VolumeLabel',$UsbVolumeLabel) }
  if ($UsbConfirmErase) { $usbArgs += @('-ConfirmErase',$UsbConfirmErase) }
  & powershell @usbArgs
  if ($LASTEXITCODE -ne 0) { throw 'USB write step failed' }
  return "USB write complete: $($UsbDriveLetter.ToUpperInvariant()):"
}

function Resolve-ReportPath([string]$ReportValue,[string]$DriveLetterValue) {
  if ($ReportValue) {
    return (Resolve-Path -LiteralPath $ReportValue).Path
  }

  if (!$DriveLetterValue) {
    throw 'Step2Finalize requires -ReportPath, or provide -UsbDriveLetter for auto-discovery.'
  }

  $root = "$($DriveLetterValue.ToUpperInvariant()):\\"
  if (!(Test-Path -LiteralPath $root)) {
    throw "USB drive not found for REPORT discovery: $root"
  }

  $candidate = Get-ChildItem -LiteralPath $root -Recurse -File -Filter 'REPORT.TXT' -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1

  if (!$candidate) {
    throw "No REPORT.TXT found on $root. Provide -ReportPath manually."
  }

  $useCandidate = $true
  if ($Host.Name -and $Host.Name -ne 'ServerRemoteHost') {
    $answer = Read-Host "Found REPORT.TXT at '$($candidate.FullName)'. Use this? (Y/N)"
    if ($answer -notmatch '^(?i:y|yes)$') {
      $manual = Read-Host 'Enter full path to REPORT.TXT'
      if ([string]::IsNullOrWhiteSpace($manual)) {
        throw 'No REPORT.TXT path provided.'
      }
      return (Resolve-Path -LiteralPath $manual).Path
    }
  }

  if ($useCandidate) {
    return $candidate.FullName
  }
}

function Maybe-ConfigureHoldTask([switch]$ForceNow,[switch]$SkipPrompt) {
  $setupHold = Join-Path $PSScriptRoot 'setup-hold-task-at-boot.ps1'
  if (!(Test-Path -LiteralPath $setupHold)) {
    return 'GPU hold setup script not found; skipped.'
  }

  $runNow = $false
  if ($ForceNow) {
    $runNow = $true
  } elseif (!$SkipPrompt -and $Host.Name -and $Host.Name -ne 'ServerRemoteHost') {
    $answer = Read-Host 'Configure GPU hold startup task now? (Y/N)'
    if ($answer -match '^(?i:y|yes)$') { $runNow = $true }
  }

  if (!$runNow) {
    return 'GPU hold startup task setup skipped by user.'
  }

  $principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
  $isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
  if (!$isAdmin) {
    return "GPU hold setup requested but requires elevation. Re-run in elevated PowerShell: powershell -NoProfile -ExecutionPolicy Bypass -File `"$setupHold`""
  }

  & powershell -NoProfile -ExecutionPolicy Bypass -File $setupHold
  if ($LASTEXITCODE -ne 0) {
    return 'GPU hold startup task setup failed. Re-run setup-hold-task-at-boot.ps1 in elevated PowerShell and review output.'
  }
  return 'GPU hold startup task configured successfully.'
}

Push-Location -LiteralPath $repo
try {
  if ($Mode -eq 'Step1Probe') {
    $assetSpec = Join-Path $repo 'unlock/assets.json'
    Ensure-AssetsReady -AssetSource $assets -SpecPath $assetSpec -PythonExe $Python

    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repo 'unlock/build.ps1') -Probe
    if ($LASTEXITCODE -ne 0) { throw 'Probe build failed' }

    $probeEfi = Join-Path $repo 'unlock/build/probe/GA107M-PROBE.EFI'
    if (!(Test-Path -LiteralPath $probeEfi)) { throw "Expected probe EFI not found: $probeEfi" }

    $stageRoot = Join-Path $repo "staging/wizard-step1-probe-$stamp"
    $bootDir = Join-Path $stageRoot 'EFI/BOOT'
    $assetOut = Join-Path $stageRoot 'RTX3090-UNLOCK/ASSETS'
    $guideOut = Join-Path $stageRoot 'GUIDANCE'
    New-Item -ItemType Directory -Path $bootDir,$guideOut -Force | Out-Null

    Copy-Item -LiteralPath $probeEfi -Destination (Join-Path $bootDir 'BOOTX64.EFI') -Force
    Copy-Assets -AssetSource $assets -AssetDest $assetOut -SpecPath $assetSpec

    @"
STEP 1 (PROBE) READY

What to do now:
1. Boot this USB from BIOS/UEFI.
2. Run the probe payload.
3. Save/copy REPORT.TXT from the USB run folder after probe completes.
4. Return to Windows and run Step 2:
   tools\new-machine-wizard.cmd
  Choose option 2 (it can auto-discover REPORT.TXT from your USB drive).

Note:
- This step only captures machine parameters. It does not perform the final unlock image build.
"@ | Set-Content -LiteralPath (Join-Path $guideOut 'NEXT-STEPS-STEP1.txt') -Encoding UTF8

    $usbSummary = Write-UsbIfRequested -SourceDir $stageRoot

    "Step 1 stage: $stageRoot"
    $usbSummary
    "After boot guidance: $guideOut\NEXT-STEPS-STEP1.txt"
    return
  }

  $report = Resolve-ReportPath -ReportValue $ReportPath -DriveLetterValue $UsbDriveLetter
  $stageRoot2 = Join-Path $repo "staging/wizard-step2-final-$stamp"

  $args = @(
    '-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $PSScriptRoot 'setup-new-machine.ps1'),
    '-ReportPath',$report,
    '-AssetsDir',$assets,
    '-Python',$Python,
    '-StockProfile',$StockProfile,
    '-StageRoot',$stageRoot2
  )
  if ($UsbDriveLetter) { $args += @('-UsbDriveLetter',$UsbDriveLetter) }
  if ($UsbFormat) { $args += '-UsbFormat' }
  if ($UsbClearExisting) { $args += '-UsbClearExisting' }
  if ($UsbVolumeLabel) { $args += @('-UsbVolumeLabel',$UsbVolumeLabel) }
  if ($UsbConfirmErase) { $args += @('-UsbConfirmErase',$UsbConfirmErase) }

  & powershell @args
  if ($LASTEXITCODE -ne 0) { throw 'Step 2 failed while generating final package.' }

  $holdSummary = Maybe-ConfigureHoldTask -ForceNow:$ConfigureHoldTaskNow -SkipPrompt:$SkipConfigureHoldPrompt
  "Step 2 complete. Final stage: $stageRoot2"
  $holdSummary
} finally {
  Pop-Location
}
