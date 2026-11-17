# Modified 2026-10-19/20 for Windows GPU UNLOCK; changes documented in CHANGES.md.
param(
 [string]$Clang='clang',
 [string]$LldLink='lld-link',
 [string]$Python='python',
 [switch]$ExperimentalUnlock,
 [switch]$BoardGGNC51518,
 [switch]$Probe,
 [string]$Profile,
 [ValidateSet('entryLatchedSs1Preserve','entryLatchedSs1Full')][string]$StockProfile='entryLatchedSs1Full'
)
$ErrorActionPreference='Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
 if(!(Test-Path -LiteralPath $PSScriptRoot)){throw 'Missing output parent'}
 if($ExperimentalUnlock -and !$PSBoundParameters.ContainsKey('StockProfile')){throw '-StockProfile is required with -ExperimentalUnlock'}
 if(!$ExperimentalUnlock -and $PSBoundParameters.ContainsKey('StockProfile')){throw '-StockProfile is only valid with -ExperimentalUnlock'}
 if($Probe -and $ExperimentalUnlock){throw '-Probe cannot combine with -ExperimentalUnlock'}
 if($Profile -and $BoardGGNC51518){throw '-Profile cannot combine with -BoardGGNC51518'}

 function Normalize-Hex([string]$Value,[string]$FieldName){
  if([string]::IsNullOrWhiteSpace($Value)){throw "Missing profile field: $FieldName"}
  $v=$Value.Trim().ToLowerInvariant()
  if($v.StartsWith('0x')){$v=$v.Substring(2)}
  if($v -notmatch '^[0-9a-f]+$'){throw "Invalid hex value for ${FieldName}: $Value"}
  return "0x${v}"
 }

 $profileObj=$null
 if($Profile){
  $profilePath=(Resolve-Path -LiteralPath $Profile).Path
  $profileObj=Get-Content -LiteralPath $profilePath -Raw | ConvertFrom-Json
  if($profileObj.schema_version -ne 1){throw "Unsupported profile schema_version: $($profileObj.schema_version)"}
  if(!$profileObj.board -or !$profileObj.igpu -or !$profileObj.bar0 -or !$profileObj.cold_pins){throw 'Profile is missing required sections'}
 }

 $out=if($ExperimentalUnlock -and $BoardGGNC51518){"build/experimental-ggnc51518/$StockProfile"}elseif($ExperimentalUnlock){"build/experimental/$StockProfile"}elseif($BoardGGNC51518 -and $Probe){'build/ggnc51518-probe'}elseif($BoardGGNC51518){'build/ggnc51518'}elseif($Probe){'build/probe'}else{'build'}
 $name=if($ExperimentalUnlock){"GA107M-EXPERIMENTAL-$StockProfile"}elseif($Probe){'GA107M-PROBE'}else{'GA107M-PREFLIGHT'}
 $defines=@()
  if($ExperimentalUnlock){$defines+= '-DGU_EXPERIMENTAL_3090';$defines+=$(switch($StockProfile){'entryLatchedSs1Preserve'{'-DGU_ENTRY_LATCHED_SS1_PRESERVE'} 'entryLatchedSs1Full'{'-DGU_ENTRY_LATCHED_SS1_FULL'}})}
  if($BoardGGNC51518){$defines+= '-DGU_BOARD_GGNC51518'}
  if($Probe){$defines+= '-DGU_PROBE_DECODE'}
 $targetPciHex='0x25A010de'
 if($BoardGGNC51518){$targetPciHex='0x25A210de'}
 if($profileObj){
  $targetPciHex=Normalize-Hex -Value $profileObj.board.target_pci_id -FieldName 'board.target_pci_id'
  $defines+='-DGU_PROFILE_EXTERNAL'
  $defines+="-DGU_BOARD_ID=$((Normalize-Hex -Value $profileObj.board.id -FieldName 'board.id'))U"
  $defines+="-DGU_BOARD_SUBSYSTEM=$((Normalize-Hex -Value $profileObj.board.subsystem -FieldName 'board.subsystem'))U"
  $defines+="-DGU_BOARD_CLASS_DWORD=$((Normalize-Hex -Value $profileObj.board.class_dword -FieldName 'board.class_dword'))U"
  $defines+="-DGU_IGPU_ID=$((Normalize-Hex -Value $profileObj.igpu.id -FieldName 'igpu.id'))U"
  $defines+="-DGU_IGPU_SUBSYSTEM=$((Normalize-Hex -Value $profileObj.igpu.subsystem -FieldName 'igpu.subsystem'))U"
  $defines+="-DGU_BOARD_PROM_SHA256=`"$($profileObj.prom_sha256)`""
  $defines+="-DGU_BAR0_BASE=$((Normalize-Hex -Value $profileObj.bar0.base -FieldName 'bar0.base'))ULL"
  $defines+="-DGU_CARD_PROFILE_PROM_SHA256=`"$($profileObj.prom_sha256)`""
  $defines+="-DGU_EXPECTED_CHIP=$((Normalize-Hex -Value $profileObj.cold_pins.chip -FieldName 'cold_pins.chip'))u"
  $defines+="-DGU_EXPECTED_GFW_PLM=$((Normalize-Hex -Value $profileObj.cold_pins.gfw_plm -FieldName 'cold_pins.gfw_plm'))u"
  $defines+="-DGU_EXPECTED_GFW_BOOT=$((Normalize-Hex -Value $profileObj.cold_pins.gfw_boot -FieldName 'cold_pins.gfw_boot'))u"
  $defines+="-DGU_EXPECTED_GFW_CPUCTL=$((Normalize-Hex -Value $profileObj.cold_pins.gfw_cpuctl -FieldName 'cold_pins.gfw_cpuctl'))u"
  $defines+="-DGU_EXPECTED_WPR2_HI=$((Normalize-Hex -Value $profileObj.cold_pins.wpr2_hi -FieldName 'cold_pins.wpr2_hi'))u"
  $defines+="-DGU_EXPECTED_FEAT_PLM=$((Normalize-Hex -Value $profileObj.cold_pins.feat_plm -FieldName 'cold_pins.feat_plm'))u"
  $defines+="-DGU_DEVICE_MEMORY_SIZE=$((Normalize-Hex -Value $profileObj.cold_pins.memory -FieldName 'cold_pins.memory'))U"
 }
 $targetPciDefine="-DGU_TARGET_PCI_ID=${targetPciHex}U"
 New-Item -ItemType Directory -Path $out -Force | Out-Null
 $sources=@('app','app_io','ownership','format','runtime','sha256','checks','port','compat',
  'vendor/unlock_610','vendor/prep_610','vendor/layout_610','vendor/vbios_610','vendor/gfw_eval')
 $objects=@()
 foreach($source in $sources) {
  $object="$out/"+($source -replace '/','_')+'.obj'
  & $Clang -target x86_64-pc-windows-msvc -std=c11 -ffreestanding -fno-builtin -fno-stack-protector -mno-red-zone -fshort-wchar -Os -Wall -Wextra -Werror -Wno-unused-parameter -D__KERNEL__ -DGU_UEFI_PORT $targetPciDefine @defines -I compat -I ../deps/edk2/MdePkg/Include -I ../deps/edk2/MdePkg/Include/X64 -c "$source.c" -o $object
  if($LASTEXITCODE -ne 0){throw "Compile failed: $source"};$objects+=$object
 }
 & $LldLink /nologo /nodefaultlib /subsystem:efi_application /entry:efi_main /machine:x64 /base:0 /dynamicbase /nxcompat /timestamp:0 /opt:ref /opt:icf "/map:$out/$name.map" "/out:$out/$name.EFI" @objects
 if($LASTEXITCODE -ne 0){throw 'EFI link failed'}
 & $Python verify_pe.py "$out/$name.EFI" "$out/$name.map" --output "$out/pe-verification.json"
 if($LASTEXITCODE -ne 0){throw 'PE verification failed'}
 [ordered]@{BuiltUtc=[DateTime]::UtcNow.ToString('o');TargetPci=($targetPciHex.Replace('0x','').ToUpperInvariant());Subsystem=$(if($profileObj){(Normalize-Hex -Value $profileObj.board.subsystem -FieldName 'board.subsystem').Replace('0x','').ToUpperInvariant()}elseif($BoardGGNC51518){'11401D05'}else{'88E5103C'});
  Mode=$(if($ExperimentalUnlock){'experimental-exact-card-manual-unlock'}else{'diagnostic-inventory-preflight-only'});
  CanArm=[bool]$ExperimentalUnlock;Defines=$defines;ManualConfirmation='U once; Esc or 30-second timeout cancels';
  StockProfile=$(if($ExperimentalUnlock){$StockProfile}else{$null});ColdChipExpected='from GU_EXPECTED_CHIP (pinned from preflight REPORT)';ColdSS0Expected=$(if($StockProfile -like 'entryLatchedSs1*'){'first fully validated cold entry; immutable'}elseif($ExperimentalUnlock){'n/a - entry latched profiles only'}else{'unknown'});
  ColdSS1Expected=$(if($StockProfile -like 'entryLatchedSs1*'){'first fully validated cold entry <= 0xf; immutable'}else{'unknown'});
  SS0Target='0x88888888';SS1Target=$(if($StockProfile -eq 'entryLatchedSs1Preserve'){'preserve entry; no SS1 writes'}elseif($StockProfile -eq 'entryLatchedSs1Full'){'0x8; one guarded write'}elseif($ExperimentalUnlock){'0x8'}else{'no writes'});
  OriginalBaselineCapture='GA107M-PREFLIGHT/RUN00/REPORT.TXT';
  StockProfileSource=$(if($StockProfile -like 'entryLatchedSs1*'){'Explicit immutable validated entry latch; no fixed stock selector assumption'}else{'preflight has no selected stock profile'});
    PromSha256=$(if($profileObj){$profileObj.prom_sha256}elseif($ExperimentalUnlock){'pinned in port.h GU_CARD_PROFILE_PROM_SHA256 from this card RUN00'}else{'pending first preflight'});
    ProfilePath=$(if($Profile){$profilePath}else{$null});
    ProfileSha256=$(if($Profile){(Get-FileHash -LiteralPath $profilePath -Algorithm SHA256).Hash}else{$null});
  DeviceMemorySize='0x1000';HardwareTested=$false;
  EfiSha256=(Get-FileHash -LiteralPath "$out/$name.EFI" -Algorithm SHA256).Hash;
  Headers=@(Get-ChildItem -LiteralPath . -Recurse -File -Filter '*.h' | Where-Object {$_.FullName -notlike '*\build\*'} | Sort-Object FullName | ForEach-Object {[ordered]@{Path=$_.FullName.Substring($PSScriptRoot.Length+1);Sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash}});
  Sources=@($sources | ForEach-Object {[ordered]@{Path="$_.c";Sha256=(Get-FileHash -LiteralPath "$_.c" -Algorithm SHA256).Hash}})
  } | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 -LiteralPath "$out/build-manifest.json"
 "Built $name.EFI; ExperimentalUnlock=$([bool]$ExperimentalUnlock); hardware untested."
} finally {Pop-Location}
