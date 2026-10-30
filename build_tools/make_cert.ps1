<#
 Create a SELF-SIGNED code-signing certificate as a PFX file (for your own machines / testing).
   $env:ATLAS_PFX_PASSWORD = "choose-a-password"
   .\build_tools\make_cert.ps1 -Out C:\keys\atlas-selfsigned.pfx
 The certificate is exported and then removed from the Windows certificate store; nothing is added to any trust store.
 To make your own PC trust it you would have to import it manually - that is a security decision this script does not make for you.
#>
param([Parameter(Mandatory = $true)][string]$Out, [string]$Subject = "CN=Atlas Library (self-signed)")
$ErrorActionPreference = "Stop"
if (-not $env:ATLAS_PFX_PASSWORD) { throw "Set `$env:ATLAS_PFX_PASSWORD first." }
$cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject $Subject -CertStoreLocation Cert:\CurrentUser\My -NotAfter (Get-Date).AddYears(3)
try {
  $pwd = ConvertTo-SecureString $env:ATLAS_PFX_PASSWORD -AsPlainText -Force
  Export-PfxCertificate -Cert $cert -FilePath $Out -Password $pwd | Out-Null
} finally { Remove-Item "Cert:\CurrentUser\My\$($cert.Thumbprint)" -Force }
Write-Host "Written: $Out"
