param(
  [string]$DevEcoHome = 'D:\devecostudio-windows-6.1.0.830\DevEco Studio'
)

$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') {
  throw 'This helper uses Windows CurrentUser DPAPI; run it on the developer Windows account.'
}
$taskKeytool = Join-Path $DevEcoHome 'jbr\bin\keytool.exe'
if (-not (Test-Path -LiteralPath $taskKeytool -PathType Leaf)) {
  throw 'The installed DevEco keytool was not found.'
}

$taskSigningRoot = Join-Path $PSScriptRoot '.signing\release'
$taskPublicRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\release\signing-request'))
$taskKeystore = Join-Path $taskSigningRoot 'spineinsight-release.p12'
$taskPasswordStore = Join-Path $taskSigningRoot 'keystore-password.dpapi'
$taskCsr = Join-Path $taskSigningRoot 'SpineInsight-release.csr'
$taskPublicCsr = Join-Path $taskPublicRoot 'SpineInsight-release.csr'
$taskAlias = 'spineinsight-release'
$taskTempPassword = Join-Path $taskSigningRoot 'keytool-password.tmp'
$taskKeytoolLog = Join-Path $taskSigningRoot 'keytool-generation.log'

# This directory is ignored by Git. Remove inherited access before any secret
# is written; only the current Windows user receives explicit file access.
New-Item -ItemType Directory -Path $taskSigningRoot -Force | Out-Null
$taskSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$taskPrivateAcl = [Security.AccessControl.DirectorySecurity]::new()
$taskPrivateAcl.SetAccessRuleProtection($true, $false)
$taskPrivateAcl.SetOwner($taskSid)
$taskInheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [Security.AccessControl.InheritanceFlags]::ObjectInherit
$taskPrivateAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
  $taskSid, [Security.AccessControl.FileSystemRights]::FullControl,
  $taskInheritance, [Security.AccessControl.PropagationFlags]::None,
  [Security.AccessControl.AccessControlType]::Allow
))
Set-Acl -LiteralPath $taskSigningRoot -AclObject $taskPrivateAcl
New-Item -ItemType Directory -Path $taskPublicRoot -Force | Out-Null

$taskExisting = @($taskKeystore, $taskPasswordStore, $taskCsr) | Where-Object { Test-Path -LiteralPath $_ }
if ($taskExisting.Count -gt 0) {
  if ($taskExisting.Count -ne 3) {
    throw 'Existing release-signing state is incomplete. It was preserved; inspect locally before retrying.'
  }
  Copy-Item -LiteralPath $taskCsr -Destination $taskPublicCsr -Force
  Write-Output ('Public CSR ready: ' + $taskPublicCsr)
  exit 0
}

$taskRandomBytes = [byte[]]::new(32)
[Security.Cryptography.RandomNumberGenerator]::Fill($taskRandomBytes)
$taskPlainPassword = [Convert]::ToBase64String($taskRandomBytes) + 'Aa9!'
[Array]::Clear($taskRandomBytes, 0, $taskRandomBytes.Length)
$taskSecurePassword = ConvertTo-SecureString -String $taskPlainPassword -AsPlainText -Force
try {
  $taskEncryptedPassword = ConvertFrom-SecureString -SecureString $taskSecurePassword
  [IO.File]::WriteAllText($taskPasswordStore, $taskEncryptedPassword, [Text.UTF8Encoding]::new($false))
  # JDK :file password modifiers keep plaintext passwords out of process arguments.
  [IO.File]::WriteAllText($taskTempPassword, $taskPlainPassword, [Text.UTF8Encoding]::new($false))
  $taskPlainPassword = $null
  & $taskKeytool '-J-Dfile.encoding=UTF-8' '-genkeypair' '-alias' $taskAlias '-keyalg' 'EC' '-groupname' 'secp256r1' '-sigalg' 'SHA256withECDSA' '-dname' 'CN=SpineInsight Release,OU=Mobile,O=Personal,C=CN' '-validity' '3650' '-storetype' 'PKCS12' '-keystore' $taskKeystore '-storepass:file' $taskTempPassword '-keypass:file' $taskTempPassword *> $taskKeytoolLog
  if ($LASTEXITCODE -ne 0) { throw 'Keystore generation failed. Details remain in the private signing directory.' }
  & $taskKeytool '-J-Dfile.encoding=UTF-8' '-certreq' '-alias' $taskAlias '-sigalg' 'SHA256withECDSA' '-keystore' $taskKeystore '-storetype' 'PKCS12' '-storepass:file' $taskTempPassword '-keypass:file' $taskTempPassword '-file' $taskCsr *>> $taskKeytoolLog
  if ($LASTEXITCODE -ne 0) { throw 'CSR generation failed. Existing private material was preserved.' }
  & $taskKeytool '-J-Dfile.encoding=UTF-8' '-printcertreq' '-file' $taskCsr *>> $taskKeytoolLog
  if ($LASTEXITCODE -ne 0) { throw 'CSR validation failed.' }
  Copy-Item -LiteralPath $taskCsr -Destination $taskPublicCsr -Force
  Write-Output ('Public CSR created and validated: ' + $taskPublicCsr)
} finally {
  if (Test-Path -LiteralPath $taskTempPassword) { Remove-Item -LiteralPath $taskTempPassword -Force }
  $taskPlainPassword = $null
  $taskSecurePassword.Dispose()
}
