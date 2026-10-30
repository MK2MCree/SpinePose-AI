param(
  [string]$DevEcoHome = 'D:\devecostudio-windows-6.1.0.830\DevEco Studio'
)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Windows CurrentUser DPAPI is required.' }
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskPrivate = Join-Path $taskRoot '.signing'
$taskKeystore = Join-Path $taskPrivate 'spineinsight-android-release.p12'
$taskPasswordFile = Join-Path $taskPrivate 'release-password.dpapi'
$taskKeytool = Join-Path $DevEcoHome 'jbr\bin\keytool.exe'
if (-not (Test-Path -LiteralPath $taskKeytool -PathType Leaf)) { throw 'DevEco keytool is missing.' }

# This is a separate Android signing key; never access or alter the HarmonyOS key.
$taskExisting = @($taskKeystore, $taskPasswordFile) | Where-Object { Test-Path -LiteralPath $_ }
if ($taskExisting.Count -eq 2) {
  $taskCurrentAcl = Get-Acl -LiteralPath $taskPrivate
  $taskOwnerSid = ([Security.Principal.NTAccount]::new($taskCurrentAcl.Owner)).Translate([Security.Principal.SecurityIdentifier])
  $taskSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
  $taskRules = @($taskCurrentAcl.Access)
  if (-not $taskCurrentAcl.AreAccessRulesProtected -or $taskOwnerSid -ne $taskSid -or $taskRules.Count -ne 1 -or
      $taskRules[0].IdentityReference.Translate([Security.Principal.SecurityIdentifier]) -ne $taskSid -or
      $taskRules[0].AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
      ($taskRules[0].FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -ne [Security.AccessControl.FileSystemRights]::FullControl) {
    throw 'Existing Android signing directory permissions need inspection; the key was preserved.'
  }
  & git -C $taskRoot check-ignore -q -- '.signing/release-password.dpapi'
  if ($LASTEXITCODE -ne 0) { throw 'Existing signing directory is not Git ignored; the key was preserved.' }
  Write-Output 'Existing Android release key preserved.'
  exit 0
}
if ($taskExisting.Count -ne 0) { throw 'Incomplete existing signing state was preserved; manual inspection is needed.' }
New-Item -ItemType Directory -Path $taskPrivate -Force | Out-Null
$taskSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$taskAcl = [Security.AccessControl.DirectorySecurity]::new()
$taskAcl.SetAccessRuleProtection($true, $false)
$taskAcl.SetOwner($taskSid)
$taskInheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [Security.AccessControl.InheritanceFlags]::ObjectInherit
$taskAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($taskSid,
  [Security.AccessControl.FileSystemRights]::FullControl, $taskInheritance,
  [Security.AccessControl.PropagationFlags]::None, [Security.AccessControl.AccessControlType]::Allow))
Set-Acl -LiteralPath $taskPrivate -AclObject $taskAcl
[IO.File]::WriteAllText((Join-Path $taskPrivate '.gitignore'), "*`n", [Text.UTF8Encoding]::new($false))
& git -C $taskRoot check-ignore -q -- '.signing/release-password.dpapi'
if ($LASTEXITCODE -ne 0) { throw 'Refusing to create signing material: Git ignore protection is missing.' }

$taskRandom = [byte[]]::new(32)
[Security.Cryptography.RandomNumberGenerator]::Fill($taskRandom)
$taskPassword = [Convert]::ToBase64String($taskRandom) + 'Aa9!'
[Array]::Clear($taskRandom, 0, $taskRandom.Length)
$taskSecure = ConvertTo-SecureString -String $taskPassword -AsPlainText -Force
try {
  $taskEncrypted = ConvertFrom-SecureString -SecureString $taskSecure
  [IO.File]::WriteAllText($taskPasswordFile, $taskEncrypted, [Text.UTF8Encoding]::new($false))
  # keytool reads the password from a child environment variable, not argv or a file.
  $env:SPINEINSIGHT_ANDROID_KEY_PASS = $taskPassword
  $taskOutput = & $taskKeytool '-J-Dfile.encoding=UTF-8' '-genkeypair' '-alias' 'spineinsight-android-release' '-keyalg' 'RSA' '-keysize' '3072' '-sigalg' 'SHA256withRSA' '-dname' 'CN=SpineInsight Android Release,OU=Mobile,O=Personal,C=CN' '-validity' '10000' '-storetype' 'PKCS12' '-keystore' $taskKeystore '-storepass:env' 'SPINEINSIGHT_ANDROID_KEY_PASS' '-keypass:env' 'SPINEINSIGHT_ANDROID_KEY_PASS' 2>&1
  $taskExit = $LASTEXITCODE
  if ($taskExit -ne 0) {
    $taskSafe = ($taskOutput | Out-String).Replace($taskPassword, '[REDACTED]')
    [IO.File]::WriteAllText((Join-Path $taskPrivate 'keytool-error.log'), $taskSafe)
    throw 'Android key generation failed. Existing signing state was preserved.'
  }
  Write-Output 'Android release key created in the local protected signing directory.'
} finally {
  Remove-Item Env:SPINEINSIGHT_ANDROID_KEY_PASS -ErrorAction SilentlyContinue
  $taskPassword = $null
  $taskSecure.Dispose()
}
