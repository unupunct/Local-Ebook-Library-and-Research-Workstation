<#
 Authenticode-sign the executable (and the installer, if present) with a PFX certificate.
   .\build_tools\sign.ps1 -Pfx C:\keys\mycert.pfx            # password read from $env:ATLAS_PFX_PASSWORD
 A self-signed certificate (see make_cert.ps1) signs the file but Windows will still show "unknown publisher"
 on other PCs; a certificate from a public CA is needed to remove SmartScreen warnings.
 This script never installs certificates into any trust store.
#>
param(
  [Parameter(Mandatory = $true)][string]$Pfx,
  [string[]]$Files = @("dist\AtlasLibrary\AtlasLibrary.exe"),
  [string]$TimestampServer = "http://timestamp.digicert.com"
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not $env:ATLAS_PFX_PASSWORD) { throw "Set `$env:ATLAS_PFX_PASSWORD to the PFX password first." }
$pwd = ConvertTo-SecureString $env:ATLAS_PFX_PASSWORD -AsPlainText -Force
$cert = Get-PfxCertificate -FilePath $Pfx -Password $pwd
$Files += Get-ChildItem dist\AtlasLibrary-Setup-*.exe -ErrorAction SilentlyContinue | ForEach-Object FullName
foreach ($f in $Files) {
  if (-not (Test-Path $f)) { Write-Host "skip (missing): $f"; continue }
  try { $r = Set-AuthenticodeSignature -FilePath $f -Certificate $cert -HashAlgorithm SHA256 -TimestampServer $TimestampServer }
  catch { Write-Warning "Timestamp server unreachable; signing without timestamp."; $r = Set-AuthenticodeSignature -FilePath $f -Certificate $cert -HashAlgorithm SHA256 }
  Write-Host ("{0}: {1}" -f $f, $r.Status)
}
