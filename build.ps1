<#
 Clean, reproducible Windows build of Atlas Library.
   .\build.ps1            -> dist\AtlasLibrary\AtlasLibrary.exe  +  dist\AtlasLibrary-1.0.0-portable.zip
 Requires: Python 3.12+ on PATH.  Creates/uses .venv.  Runs the test-suite first; stops on failure.
 Optional installer: if Inno Setup (ISCC.exe) is installed, also builds dist\AtlasLibrary-Setup-1.0.0.exe from build_tools\installer.iss
#>
param([string]$Pfx = "")   # optional: sign the exe with this PFX (password in $env:ATLAS_PFX_PASSWORD)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$version = (Select-String -Path atlas\__init__.py -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value
if (-not (Test-Path .venv)) { python -m venv .venv }
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r requirements-dev.txt
& $py build_tools\make_icon.py
Write-Host "== Running tests"
& $py -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Tests failed - build aborted" }
Write-Host "== Cleaning"
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
Write-Host "== PyInstaller"
& $py -m PyInstaller --noconfirm --clean AtlasLibrary.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
if ($Pfx) { & .\build_tools\sign.ps1 -Pfx $Pfx }
$zip = "dist\AtlasLibrary-$version-portable.zip"
Compress-Archive -Path dist\AtlasLibrary\* -DestinationPath $zip -Force
Write-Host "Portable build: $zip"
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "$env:ProgramFiles (x86)\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) { & $iscc /DAppVersion=$version build_tools\installer.iss; if ($Pfx) { & .\build_tools\sign.ps1 -Pfx $Pfx -Files @() } } else { Write-Host "Inno Setup not found - skipping installer (portable zip is ready)." }
Write-Host "Done."
