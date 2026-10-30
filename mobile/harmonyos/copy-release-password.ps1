# Run manually in a private local PowerShell session when configuring DevEco.
# Never run this helper for the purpose of returning a password to chat or logs.
$ErrorActionPreference = 'Stop'
$taskSecretFile = Join-Path $PSScriptRoot '.signing\release\keystore-password.dpapi'
if (-not (Test-Path -LiteralPath $taskSecretFile -PathType Leaf)) { throw 'Prepare release signing first.' }
$taskSecure = Get-Content -LiteralPath $taskSecretFile -Raw | ConvertTo-SecureString
try {
  $taskCredential = [Net.NetworkCredential]::new('', $taskSecure)
  Set-Clipboard -Value $taskCredential.Password
  Write-Output 'The keystore password was copied locally. Paste it only into the DevEco signing fields; clear the clipboard afterwards.'
} finally {
  $taskCredential = $null
  $taskSecure.Dispose()
}
