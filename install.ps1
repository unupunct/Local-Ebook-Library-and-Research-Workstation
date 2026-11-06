<#
 Atlas Library - one-line installer for Windows 10/11.

   irm https://raw.githubusercontent.com/unupunct/Local-Ebook-Library-and-Research-Workstation/main/install.ps1 | iex

 Downloads the newest installer from the GitHub release page, shows its SHA-256, and runs it (per-user install, no
 administrator rights needed). Nothing else is changed. To pass options, save the script and run it:
   .\install.ps1 -Silent          install without showing the wizard
   .\install.ps1 -Portable        unzip the portable build to %LOCALAPPDATA%\Programs\AtlasLibrary instead
   .\install.ps1 -Version 1.2.0   a specific release instead of the latest
#>
param([switch]$Silent, [switch]$Portable, [string]$Version = "latest")
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$repo = "unupunct/Local-Ebook-Library-and-Research-Workstation"
$api = if ($Version -eq "latest") { "https://api.github.com/repos/$repo/releases/latest" } else { "https://api.github.com/repos/$repo/releases/tags/v$($Version.TrimStart('v'))" }
Write-Host "==> Looking up the release ($Version)"
$rel = Invoke-RestMethod -Uri $api -Headers @{ "User-Agent" = "atlas-library-installer" }
$pattern = if ($Portable) { "AtlasLibrary-*-portable.zip" } else { "AtlasLibrary-Setup-*.exe" }
$asset = $rel.assets | Where-Object { $_.name -like $pattern } | Select-Object -First 1
if (-not $asset) { throw "No asset matching $pattern in release $($rel.tag_name)." }
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("atlas-install-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
  $file = Join-Path $tmp $asset.name
  Write-Host "==> Downloading $($asset.name) ($([math]::Round($asset.size / 1MB, 1)) MB)"
  Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $file -UseBasicParsing
  Write-Host "    SHA-256: $((Get-FileHash $file -Algorithm SHA256).Hash)"
  if ($Portable) {
    $dest = Join-Path $env:LOCALAPPDATA "Programs\AtlasLibrary"
    Write-Host "==> Unpacking to $dest"
    if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
    Expand-Archive -Path $file -DestinationPath $dest
    Write-Host "Done. Start it with: $dest\AtlasLibrary.exe"
  } else {
    Write-Host "==> Running the installer (the build is unsigned: SmartScreen may ask you to confirm)"
    $args = @(); if ($Silent) { $args += "/SILENT" }
    Start-Process -FilePath $file -ArgumentList $args -Wait
    Write-Host "Done. Atlas Library is in your Start menu."
  }
} finally {
  Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
Write-Host "Your library data stays in %LOCALAPPDATA%\AtlasLibrary; uninstall from Settings > Apps."
