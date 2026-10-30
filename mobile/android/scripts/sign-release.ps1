param(
  [Parameter(Mandatory = $true)] [string]$UnsignedApk,
  [Parameter(Mandatory = $true)] [string]$OutputApk,
  [string]$SdkRoot = (Join-Path $PSScriptRoot '..\..\..\.runtime\android-toolchain\sdk'),
  [string]$DevEcoHome = 'D:\devecostudio-windows-6.1.0.830\DevEco Studio'
)
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskPrivate = Join-Path $taskRoot '.signing'
$taskKeystore = Join-Path $taskPrivate 'spineinsight-android-release.p12'
$taskPasswordFile = Join-Path $taskPrivate 'release-password.dpapi'
$taskJava = Join-Path $DevEcoHome 'jbr\bin\java.exe'
$taskSigner = Join-Path $SdkRoot 'build-tools\35.0.0\lib\apksigner.jar'
foreach ($taskFile in @($UnsignedApk, $taskKeystore, $taskPasswordFile, $taskJava, $taskSigner)) {
  if (-not (Test-Path -LiteralPath $taskFile -PathType Leaf)) { throw 'A required APK, key, password, or SDK tool is missing.' }
}
$taskInput = (Resolve-Path -LiteralPath $UnsignedApk).Path
$taskOutput = [IO.Path]::GetFullPath($OutputApk)
if ($taskInput -eq $taskOutput -or (Test-Path -LiteralPath $taskOutput)) { throw 'Output already exists or matches input; refusing to overwrite.' }
$taskTemp = Join-Path $taskPrivate ('signed-' + [guid]::NewGuid().ToString('N') + '.apk')
$taskInfo = [Diagnostics.ProcessStartInfo]::new()
$taskInfo.FileName = $taskJava
$taskInfo.UseShellExecute = $false
$taskInfo.CreateNoWindow = $true
$taskInfo.RedirectStandardInput = $true
$taskInfo.RedirectStandardOutput = $true
$taskInfo.RedirectStandardError = $true
$taskInfo.StandardInputEncoding = [Text.UTF8Encoding]::new($false)
foreach ($taskArg in @('-Dfile.encoding=UTF-8', '-jar', $taskSigner, 'sign', '--ks', $taskKeystore,
    '--ks-type', 'PKCS12', '--ks-key-alias', 'spineinsight-android-release', '--ks-pass', 'stdin',
    '--debuggable-apk-permitted', 'false', '--in', $taskInput, '--out', $taskTemp)) {
  $taskInfo.ArgumentList.Add($taskArg)
}
$taskSecure = Get-Content -LiteralPath $taskPasswordFile -Raw | ConvertTo-SecureString
$taskProcess = [Diagnostics.Process]::new()
try {
  $taskProcess.StartInfo = $taskInfo
  if (-not $taskProcess.Start()) { throw 'Could not launch the Android SDK signing tool.' }
  $taskStdout = $taskProcess.StandardOutput.ReadToEndAsync()
  $taskStderr = $taskProcess.StandardError.ReadToEndAsync()
  $taskCredential = [Net.NetworkCredential]::new('', $taskSecure)
  $taskProcess.StandardInput.WriteLine($taskCredential.Password)
  $taskProcess.StandardInput.Close()
  $taskCredential = $null
  if (-not $taskProcess.WaitForExit(120000)) { $taskProcess.Kill($true); throw 'Android signing timed out.' }
  $taskSignExit = $taskProcess.ExitCode
  $null = $taskStdout.GetAwaiter().GetResult()
  $null = $taskStderr.GetAwaiter().GetResult()
  if ($taskSignExit -ne 0 -or -not (Test-Path -LiteralPath $taskTemp -PathType Leaf)) {
    throw 'Android signing failed; no output was copied. Check the input is a release APK.'
  }
  & $taskJava '-Dfile.encoding=UTF-8' '-jar' $taskSigner 'verify' '--verbose' $taskTemp
  if ($LASTEXITCODE -ne 0) { throw 'Android SDK signature verification failed.' }
  $taskOutputDirectory = [IO.Path]::GetDirectoryName($taskOutput)
  if (-not (Test-Path -LiteralPath $taskOutputDirectory -PathType Container)) { throw 'Output directory does not exist.' }
  Copy-Item -LiteralPath $taskTemp -Destination $taskOutput
  Write-Output ('Verified release APK: ' + $taskOutput)
} finally {
  $taskCredential = $null
  $taskSecure.Dispose()
  $taskProcess.Dispose()
  if (Test-Path -LiteralPath $taskTemp -PathType Leaf) { Remove-Item -LiteralPath $taskTemp -Force }
}
