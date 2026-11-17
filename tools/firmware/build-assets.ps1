param(
  [string]$Python = 'python',
  [string]$OutputDir = '',
  [string]$WorkDir = '',
  [switch]$Offline,
  [switch]$Clean
)

$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if (-not $OutputDir) { $OutputDir = Join-Path $repoRoot 'assets' }
if (-not $WorkDir) { $WorkDir = Join-Path $repoRoot 'staging\firmware-work' }

$outputDirResolved = [System.IO.Path]::GetFullPath($OutputDir)
$workDirResolved = [System.IO.Path]::GetFullPath($WorkDir)
$manifestPath = Join-Path $PSScriptRoot 'artifacts-610.43.03.json'
$assetSpecPath = Join-Path $repoRoot 'unlock\assets.json'

if (!(Test-Path -LiteralPath $manifestPath)) { throw "Missing manifest: $manifestPath" }
if (!(Test-Path -LiteralPath $assetSpecPath)) { throw "Missing asset spec: $assetSpecPath" }

$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$sourceFilename = $manifest.source_artifacts[0].filename
$pkgFilename = $manifest.source_artifacts[2].filename

$artifactsDir = Join-Path $workDirResolved 'artifacts'
$sourceExtractDir = Join-Path $workDirResolved 'source-tree'
$generatedDir = Join-Path $workDirResolved 'generated-assets'

if ($Clean -and (Test-Path -LiteralPath $workDirResolved)) {
  Remove-Item -LiteralPath $workDirResolved -Recurse -Force
}

New-Item -ItemType Directory -Path $artifactsDir,$generatedDir,$outputDirResolved -Force | Out-Null

$pyCmd = Get-Command $Python -ErrorAction SilentlyContinue
if (!$pyCmd) { throw "Python not found: $Python" }
$py = $pyCmd.Source

Push-Location -LiteralPath $PSScriptRoot
try {
  $fetchArgs = @('fetch_artifacts.py', $artifactsDir)
  if ($Offline) { $fetchArgs += '--offline' }
  & $py @fetchArgs
  if ($LASTEXITCODE -ne 0) { throw 'fetch_artifacts.py failed' }

  $sourceArchive = Join-Path $artifactsDir $sourceFilename
  $utilsPackage = Join-Path $artifactsDir $pkgFilename
  if (!(Test-Path -LiteralPath $sourceArchive)) { throw "Missing source archive after fetch: $sourceArchive" }
  if (!(Test-Path -LiteralPath $utilsPackage)) { throw "Missing package after fetch: $utilsPackage" }

  if (Test-Path -LiteralPath $sourceExtractDir) {
    Remove-Item -LiteralPath $sourceExtractDir -Recurse -Force
  }
  New-Item -ItemType Directory -Path $sourceExtractDir -Force | Out-Null

  & tar -xf $sourceArchive -C $sourceExtractDir
  if ($LASTEXITCODE -ne 0) { throw 'Failed to extract NVIDIA source archive (tar -xf)' }

  $sourceRoot = $null
  foreach ($dir in (Get-ChildItem -LiteralPath $sourceExtractDir -Directory)) {
    if (Test-Path -LiteralPath (Join-Path $dir.FullName 'src\nvidia\generated')) {
      $sourceRoot = $dir.FullName
      break
    }
  }
  if (!$sourceRoot -and (Test-Path -LiteralPath (Join-Path $sourceExtractDir 'src\nvidia\generated'))) {
    $sourceRoot = $sourceExtractDir
  }
  if (!$sourceRoot) {
    throw "Could not locate extracted NVIDIA source root under $sourceExtractDir"
  }

  & $py 'generate_v67.py' $utilsPackage $generatedDir
  if ($LASTEXITCODE -ne 0) { throw 'generate_v67.py failed' }

  & $py 'extract_boot_assets.py' $sourceRoot $generatedDir --chip ga102
  if ($LASTEXITCODE -ne 0) { throw 'extract_boot_assets.py failed' }

  $spec = Get-Content -LiteralPath $assetSpecPath -Raw | ConvertFrom-Json
  foreach ($entry in $spec) {
    $src = Join-Path $generatedDir $entry.Name
    if (!(Test-Path -LiteralPath $src)) { throw "Missing generated file: $src" }

    $size = (Get-Item -LiteralPath $src).Length
    if ($size -ne [int64]$entry.Bytes) {
      throw "Size mismatch for $($entry.Name): expected $($entry.Bytes), got $size"
    }

    $sha = (Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($sha -ne $entry.Sha256.ToLowerInvariant()) {
      throw "SHA256 mismatch for $($entry.Name): expected $($entry.Sha256), got $sha"
    }

    Copy-Item -LiteralPath $src -Destination (Join-Path $outputDirResolved $entry.Name) -Force
  }

  "Firmware assets ready in: $outputDirResolved"
  "Verified files:"
  foreach ($entry in $spec) {
    "  $($entry.Name)"
  }
} finally {
  Pop-Location
}
