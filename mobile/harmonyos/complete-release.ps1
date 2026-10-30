param(
  [Parameter(Mandatory = $true)] [string]$CertificatePath,
  [Parameter(Mandatory = $true)] [string]$ProfilePath,
  [string]$DevEcoHome = 'D:\devecostudio-windows-6.1.0.830\DevEco Studio'
)

$ErrorActionPreference = 'Stop'
$taskPrivateRoot = Join-Path $PSScriptRoot '.signing\release'
$taskKeystore = Join-Path $taskPrivateRoot 'spineinsight-release.p12'
$taskSecretFile = Join-Path $taskPrivateRoot 'keystore-password.dpapi'
$taskCertificate = Join-Path $taskPrivateRoot 'huawei-release.cer'
$taskProfile = Join-Path $taskPrivateRoot 'huawei-release.p7b'
$taskPublicRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\release'))
$taskExpectedBundle = 'com.benjaminmcree.spineinsight'
foreach ($taskInput in @($CertificatePath, $ProfilePath, $taskKeystore, $taskSecretFile)) {
  if (-not (Test-Path -LiteralPath $taskInput -PathType Leaf)) { throw 'A signing input is missing. Prepare the local CSR and download the Huawei release certificate and release Profile first.' }
}

# Parse and verify the CMS envelope without printing its content. This checks
# integrity only; it does not replace Huawei issuer trust or publication review.
Add-Type -AssemblyName System.Security.Cryptography.Pkcs
$taskCms = [Security.Cryptography.Pkcs.SignedCms]::new()
$taskCms.Decode([IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $ProfilePath)))
$taskCms.CheckSignature($true)
$taskProfileInfo = [Text.Encoding]::UTF8.GetString($taskCms.ContentInfo.Content) | ConvertFrom-Json
if ($taskProfileInfo.type -ne 'release' -or $taskProfileInfo.'bundle-info'.'bundle-name' -ne $taskExpectedBundle) {
  throw 'The Profile is not a release Profile for the expected application bundle.'
}
if ($taskProfileInfo.'app-distribution-type' -ne 'app_gallery') {
  throw 'The Profile does not declare AppGallery distribution.'
}
if ($taskProfileInfo.'bundle-info'.'app-identifier' -ne '6917619919410116692') {
  throw 'The release Profile does not match the expected Huawei APP ID.'
}
$taskNow = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
if (-not $taskProfileInfo.validity -or
    $taskProfileInfo.validity.'not-before' -gt $taskNow -or
    $taskProfileInfo.validity.'not-after' -lt $taskNow) {
  throw 'The release Profile is not currently valid.'
}

# Ensure that the downloaded certificate contains the public key requested by
# this machine, rather than a certificate for a different private keystore.
$taskSecure = Get-Content -LiteralPath $taskSecretFile -Raw | ConvertTo-SecureString
$taskLocalCertificate = $null
try {
  $taskCredential = [Net.NetworkCredential]::new('', $taskSecure)
  $taskLocalCertificate = [Security.Cryptography.X509Certificates.X509Certificate2]::new(
    $taskKeystore, $taskCredential.Password,
    [Security.Cryptography.X509Certificates.X509KeyStorageFlags]::EphemeralKeySet
  )
  $taskCertificates = [Security.Cryptography.X509Certificates.X509Certificate2Collection]::new()
  $taskCertificateText = [IO.File]::ReadAllText((Resolve-Path -LiteralPath $CertificatePath))
  if ($taskCertificateText.Contains('-----BEGIN CERTIFICATE-----')) {
    $taskCertificates.ImportFromPem($taskCertificateText)
  } else {
    $taskCertificates.Import([IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $CertificatePath)))
  }
  $taskMatchingCertificate = @($taskCertificates | Where-Object {
    $_.PublicKey.Oid.Value -eq $taskLocalCertificate.PublicKey.Oid.Value -and
    $_.GetPublicKeyString() -eq $taskLocalCertificate.GetPublicKeyString()
  })
  if ($taskMatchingCertificate.Count -ne 1) { throw 'The downloaded certificate does not match this CSR/keystore.' }
  if ($taskMatchingCertificate[0].NotAfter -lt (Get-Date) -or $taskMatchingCertificate[0].NotBefore -gt (Get-Date)) {
    throw 'The downloaded certificate is not currently valid.'
  }
  $taskProfileCertificate = [Security.Cryptography.X509Certificates.X509Certificate2]::CreateFromPem(
    $taskProfileInfo.'bundle-info'.'distribution-certificate'
  )
  try {
    if ($taskProfileCertificate.Thumbprint -ne $taskMatchingCertificate[0].Thumbprint) {
      throw 'The release Profile embeds a different distribution certificate.'
    }
  } finally { $taskProfileCertificate.Dispose() }
} finally {
  $taskCredential = $null
  if ($taskLocalCertificate) { $taskLocalCertificate.Dispose() }
  $taskSecure.Dispose()
}

if ([IO.Path]::GetFullPath($CertificatePath) -ne [IO.Path]::GetFullPath($taskCertificate)) {
  Copy-Item -LiteralPath $CertificatePath -Destination $taskCertificate -Force
}
if ([IO.Path]::GetFullPath($ProfilePath) -ne [IO.Path]::GetFullPath($taskProfile)) {
  Copy-Item -LiteralPath $ProfilePath -Destination $taskProfile -Force
}

# Build the standard unsigned APP Pack with Hvigor, then apply the same SDK
# sign-app operation used by Hvigor's SignApp task. The SDK is invoked in a JVM
# wrapper so the DPAPI-protected secret never appears in process arguments,
# build-profile.json5, or a plaintext temporary password file.
$taskNode = Join-Path $DevEcoHome 'tools\node\node.exe'
$taskJson5 = Join-Path $DevEcoHome 'tools\hvigor\hvigor-ohos-plugin\node_modules\json5'
$taskJava = Join-Path $DevEcoHome 'jbr\bin\java.exe'
$taskJavac = Join-Path $DevEcoHome 'jbr\bin\javac.exe'
$taskSignerJar = Join-Path $DevEcoHome 'sdk\default\openharmony\toolchains\lib\hap-sign-tool.jar'
foreach ($taskTool in @($taskNode, $taskJava, $taskJavac, $taskSignerJar)) {
  if (-not (Test-Path -LiteralPath $taskTool -PathType Leaf)) { throw 'An installed DevEco signing tool is missing.' }
}
$taskBuildStarted = [DateTime]::UtcNow
$taskBuildLog = Join-Path $taskPrivateRoot 'release-app-build.log'
try {
  & (Join-Path $PSScriptRoot 'build-local.ps1') -DevEcoHome $DevEcoHome -BuildMode release -BuildArtifact app *> $taskBuildLog
} catch {
  throw 'Release build failed. Check the private local build log; do not send it before reviewing for signing information.'
}
$taskOutputDirectory = Join-Path $PSScriptRoot 'build\outputs\default'
$taskUnsignedApp = Join-Path $taskOutputDirectory 'harmonyos-default-unsigned.app'
if (-not (Test-Path -LiteralPath $taskUnsignedApp -PathType Leaf)) { throw 'No unsigned release APP Pack was produced.' }
$taskClasses = Join-Path $taskPrivateRoot 'classes'
New-Item -ItemType Directory -Path $taskClasses -Force | Out-Null
& $taskJavac '-encoding' 'UTF-8' '-cp' $taskSignerJar '-d' $taskClasses (Join-Path $PSScriptRoot 'tools\ReleaseSigner.java') *> (Join-Path $taskPrivateRoot 'compile-wrapper.log')
if ($LASTEXITCODE -ne 0) { throw 'Could not compile the SDK signing wrapper.' }

$taskSignedApp = Join-Path $taskPrivateRoot 'SpineInsight-release-signed.app'
$taskProcessInfo = [Diagnostics.ProcessStartInfo]::new()
$taskProcessInfo.FileName = $taskJava
$taskProcessInfo.WorkingDirectory = $taskPrivateRoot
$taskProcessInfo.UseShellExecute = $false
$taskProcessInfo.CreateNoWindow = $true
$taskProcessInfo.RedirectStandardInput = $true
$taskProcessInfo.RedirectStandardOutput = $true
$taskProcessInfo.RedirectStandardError = $true
$taskProcessInfo.StandardInputEncoding = [Text.UTF8Encoding]::new($false)
foreach ($taskArg in @('-Dfile.encoding=UTF-8', '-cp', ($taskClasses + ';' + $taskSignerJar), 'ReleaseSigner',
    'sign-app', '-mode', 'localSign', '-keyAlias', 'spineinsight-release',
    '-appCertFile', $taskCertificate, '-profileFile', $taskProfile, '-profileSigned', '1',
    '-inFile', $taskUnsignedApp, '-outFile', $taskSignedApp,
    '-signAlg', 'SHA256withECDSA', '-keystoreFile', $taskKeystore)) {
  $taskProcessInfo.ArgumentList.Add($taskArg)
}
$taskSecure = Get-Content -LiteralPath $taskSecretFile -Raw | ConvertTo-SecureString
$taskSignerProcess = [Diagnostics.Process]::new()
try {
  $taskSignerProcess.StartInfo = $taskProcessInfo
  if (-not $taskSignerProcess.Start()) { throw 'Could not start the SDK signer.' }
  $taskStdout = $taskSignerProcess.StandardOutput.ReadToEndAsync()
  $taskStderr = $taskSignerProcess.StandardError.ReadToEndAsync()
  $taskCredential = [Net.NetworkCredential]::new('', $taskSecure)
  $taskSignerProcess.StandardInput.WriteLine($taskCredential.Password)
  $taskSignerProcess.StandardInput.Close()
  $taskCredential = $null
  if (-not $taskSignerProcess.WaitForExit(120000)) {
    $taskSignerProcess.Kill($true)
    throw 'The SDK signing operation timed out.'
  }
  $taskSignOutput = $taskStdout.GetAwaiter().GetResult()
  $taskSignError = $taskStderr.GetAwaiter().GetResult()
  if ($taskSignerProcess.ExitCode -ne 0 -or $taskSignOutput -notmatch 'SDK_SIGN_SUCCESS') {
    # ReleaseSigner redacts the stdin secret before returning diagnostics.
    [IO.File]::WriteAllText((Join-Path $taskPrivateRoot 'sdk-signing.log'), $taskSignOutput + $taskSignError)
    throw 'The official SDK signing operation failed; no submission file was copied.'
  }
} finally {
  $taskCredential = $null
  $taskSecure.Dispose()
  $taskSignerProcess.Dispose()
}
if (-not (Test-Path -LiteralPath $taskSignedApp -PathType Leaf) -or
    (Get-Item -LiteralPath $taskSignedApp).LastWriteTimeUtc -lt $taskBuildStarted.AddSeconds(-2)) {
  throw 'No fresh signed APP Pack was produced; no submission file was copied.'
}

# Verify the actual outer APP signature, just as Hvigor signs the outer App Pack.
# Inner HAPs in this standard AppGallery pack are not sideload installation files.
$taskVerifiedChain = Join-Path $taskPrivateRoot 'verified-chain.cer'
$taskVerifiedProfile = Join-Path $taskPrivateRoot 'verified-profile.p7b'
& $taskJava '-Dfile.encoding=UTF-8' '-jar' $taskSignerJar 'verify-app' '-inFile' $taskSignedApp '-outCertChain' $taskVerifiedChain '-outProfile' $taskVerifiedProfile *> (Join-Path $taskPrivateRoot 'sdk-verification.log')
if ($LASTEXITCODE -ne 0) { throw 'The official SDK could not verify the signed APP Pack.' }
if ((Get-FileHash -LiteralPath $taskVerifiedProfile -Algorithm SHA256).Hash -ne
    (Get-FileHash -LiteralPath $taskProfile -Algorithm SHA256).Hash) {
  throw 'The signed APP Pack does not embed the selected release Profile.'
}
$taskVerifiedCertificates = [Security.Cryptography.X509Certificates.X509Certificate2Collection]::new()
$taskVerifiedCertificates.ImportFromPem([IO.File]::ReadAllText($taskVerifiedChain))
if (-not @($taskVerifiedCertificates | Where-Object { $_.Thumbprint -eq $taskMatchingCertificate[0].Thumbprint }).Count) {
  throw 'The APP signature does not contain the selected release certificate.'
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
function Read-ReleaseZipJson($Archive, [string]$EntryName) {
  $taskEntry = $Archive.GetEntry($EntryName)
  if (-not $taskEntry) { throw ('Release package entry is missing: ' + $EntryName) }
  $taskReader = [IO.StreamReader]::new($taskEntry.Open())
  try { return ($taskReader.ReadToEnd() | ConvertFrom-Json) } finally { $taskReader.Dispose() }
}
$taskZip = [IO.Compression.ZipFile]::OpenRead($taskSignedApp)
try {
  $taskPack = Read-ReleaseZipJson $taskZip 'pack.info'
  $taskHaps = @($taskZip.Entries | Where-Object { $_.FullName.EndsWith('.hap') })
  if ($taskHaps.Count -ne 1) { throw 'Unexpected release module set.' }
  $taskMemory = [IO.MemoryStream]::new()
  $taskHapStream = $taskHaps[0].Open()
  try { $taskHapStream.CopyTo($taskMemory) } finally { $taskHapStream.Dispose() }
  $taskMemory.Position = 0
  $taskHap = [IO.Compression.ZipArchive]::new($taskMemory, [IO.Compression.ZipArchiveMode]::Read)
  try {
    $taskModule = Read-ReleaseZipJson $taskHap 'module.json'
    if ($taskModule.app.bundleName -ne $taskExpectedBundle -or $taskModule.app.debug -ne $false -or
        $taskModule.app.versionName -ne '0.2.0' -or $taskModule.app.versionCode -ne 2 -or
        @($taskModule.module.requestPermissions).Count -ne 0 -or
        $taskPack.summary.app.bundleName -ne $taskExpectedBundle -or
        $taskPack.summary.app.version.code -ne 2) {
      throw 'Release package identity, build mode, version, or permission check failed.'
    }
  } finally { $taskHap.Dispose(); $taskMemory.Dispose() }
} finally { $taskZip.Dispose() }

$taskFinalDirectory = Join-Path $taskPublicRoot 'signed'
New-Item -ItemType Directory -Path $taskFinalDirectory -Force | Out-Null
$taskFinalPath = Join-Path $taskFinalDirectory 'SpineInsight-0.2.0-release-signed.app'
Copy-Item -LiteralPath $taskSignedApp -Destination $taskFinalPath -Force
$taskReport = [ordered]@{
  schemaVersion = 1
  fileName = [IO.Path]::GetFileName($taskFinalPath)
  bundleName = $taskExpectedBundle
  versionName = '0.2.0'
  versionCode = 2
  buildMode = 'release'
  distribution = 'app_gallery'
  signatureScope = 'APP Pack'
  sdkSignatureVerified = $true
  embeddedProfileMatches = $true
  releaseCertificateMatches = $true
  requestPermissions = @()
  sizeBytes = (Get-Item -LiteralPath $taskFinalPath).Length
  sha256 = (Get-FileHash -LiteralPath $taskFinalPath -Algorithm SHA256).Hash.ToLowerInvariant()
}
$taskReport | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $taskFinalDirectory 'verification.json') -Encoding utf8
Write-Output ('Signed APP Pack ready: ' + $taskFinalPath)
