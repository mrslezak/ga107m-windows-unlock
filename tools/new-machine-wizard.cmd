@echo off
setlocal

set "ROOT=%~dp0"
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"

if "%~1"=="" goto :interactive

"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%ROOT%new-machine-wizard.ps1" %*
exit /b %ERRORLEVEL%

:interactive
echo.
echo GA107M New Machine Wizard
echo ========================
echo 1. Step 1 - Build PROBE USB package
echo 2. Step 2 - Build FINAL unlock USB package from REPORT.TXT
echo.
set /p CHOICE=Choose 1 or 2: 
if "%CHOICE%"=="1" goto :step1
if "%CHOICE%"=="2" goto :step2

echo Invalid choice.
exit /b 1

:step1
set /p ASSETS=Assets folder path: 
set /p USB=USB drive letter (optional, e.g. E ; leave blank for stage-only): 
if "%USB%"=="" (
  "%PS%" -NoProfile -ExecutionPolicy Bypass -File "%ROOT%new-machine-wizard.ps1" -Mode Step1Probe -AssetsDir "%ASSETS%"
) else (
  set /p CLEAR=Clear existing USB files? (Y/N): 
  if /I "%CLEAR%"=="Y" (
    "%PS%" -NoProfile -ExecutionPolicy Bypass -File "%ROOT%new-machine-wizard.ps1" -Mode Step1Probe -AssetsDir "%ASSETS%" -UsbDriveLetter "%USB%" -UsbClearExisting
  ) else (
    "%PS%" -NoProfile -ExecutionPolicy Bypass -File "%ROOT%new-machine-wizard.ps1" -Mode Step1Probe -AssetsDir "%ASSETS%" -UsbDriveLetter "%USB%"
  )
)
exit /b %ERRORLEVEL%

:step2
set /p ASSETS=Assets folder path: 
set /p USB=USB drive letter (optional, e.g. E ; enables auto REPORT discovery): 
set /p REPORT=REPORT.TXT path from Step 1 (optional if USB is set): 
set /p HOLD=Configure GPU hold startup task now? (Y/N): 
set "HOLDARG=-SkipConfigureHoldPrompt"
if /I "%HOLD%"=="Y" set "HOLDARG=-ConfigureHoldTaskNow -SkipConfigureHoldPrompt"
if "%USB%"=="" (
  if "%REPORT%"=="" (
    echo REPORT.TXT path is required when USB drive letter is not provided.
    exit /b 1
  )
  "%PS%" -NoProfile -ExecutionPolicy Bypass -File "%ROOT%new-machine-wizard.ps1" -Mode Step2Finalize -AssetsDir "%ASSETS%" -ReportPath "%REPORT%" %HOLDARG%
) else (
  set /p CLEAR=Clear existing USB files? (Y/N): 
  if /I "%CLEAR%"=="Y" (
    if "%REPORT%"=="" (
      "%PS%" -NoProfile -ExecutionPolicy Bypass -File "%ROOT%new-machine-wizard.ps1" -Mode Step2Finalize -AssetsDir "%ASSETS%" -UsbDriveLetter "%USB%" -UsbClearExisting %HOLDARG%
    ) else (
      "%PS%" -NoProfile -ExecutionPolicy Bypass -File "%ROOT%new-machine-wizard.ps1" -Mode Step2Finalize -AssetsDir "%ASSETS%" -ReportPath "%REPORT%" -UsbDriveLetter "%USB%" -UsbClearExisting %HOLDARG%
    )
  ) else (
    if "%REPORT%"=="" (
      "%PS%" -NoProfile -ExecutionPolicy Bypass -File "%ROOT%new-machine-wizard.ps1" -Mode Step2Finalize -AssetsDir "%ASSETS%" -UsbDriveLetter "%USB%" %HOLDARG%
    ) else (
      "%PS%" -NoProfile -ExecutionPolicy Bypass -File "%ROOT%new-machine-wizard.ps1" -Mode Step2Finalize -AssetsDir "%ASSETS%" -ReportPath "%REPORT%" -UsbDriveLetter "%USB%" %HOLDARG%
    )
  )
)
exit /b %ERRORLEVEL%
