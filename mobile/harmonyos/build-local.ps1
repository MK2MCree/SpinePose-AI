param(
  [string]$DevEcoHome = $env:DEVECO_HOME,
  [ValidateSet('debug', 'release')]
  [string]$BuildMode = 'debug',
  [ValidateSet('hap', 'app')]
  [string]$BuildArtifact = 'hap'
)

$ErrorActionPreference = 'Stop'
if ($BuildArtifact -eq 'app' -and -not $PSBoundParameters.ContainsKey('BuildMode')) {
  $BuildMode = 'release'
}
if ([string]::IsNullOrWhiteSpace($DevEcoHome)) {
  $taskRegistryRoots = @(
    'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
  )
  foreach ($taskRegistryRoot in $taskRegistryRoots) {
    $taskInstalledStudio = Get-ItemProperty -Path $taskRegistryRoot -ErrorAction SilentlyContinue |
      Where-Object { $_.DisplayName -eq 'DevEco Studio' -and $_.InstallLocation } |
      Select-Object -First 1
    if ($taskInstalledStudio) {
      $DevEcoHome = $taskInstalledStudio.InstallLocation
      break
    }
  }
}
if ([string]::IsNullOrWhiteSpace($DevEcoHome)) {
  throw 'DevEco Studio was not found. Pass -DevEcoHome with its installation directory.'
}
$taskNode = Join-Path $DevEcoHome 'tools\node\node.exe'
$taskWrapper = Join-Path $DevEcoHome 'tools\hvigor\hvigor\bin\hvigor.js'
foreach ($taskRequired in @($taskNode, $taskWrapper)) {
  if (-not (Test-Path -LiteralPath $taskRequired -PathType Leaf)) {
    throw "DevEco tool missing: $taskRequired"
  }
}
# Hvigor validates physical paths strictly; the user's C: workspace is a junction
# to D: on this machine. Resolve the same project directory before launching it.
$taskProjectRoot = (& $taskNode -e 'process.stdout.write(require("fs").realpathSync.native(process.argv[1]));' $PSScriptRoot).Trim()

$taskBuildEnvironment = @{
  NODE_HOME = Join-Path $DevEcoHome 'tools\node'
  JAVA_HOME = Join-Path $DevEcoHome 'jbr'
  DEVECO_SDK_HOME = Join-Path $DevEcoHome 'sdk'
  HVIGOR_USER_HOME = Join-Path $taskProjectRoot '.build-tools\hvigor-home'
  NODE_PATH = Join-Path $taskProjectRoot 'node_modules'
  PATH = ((Join-Path $DevEcoHome 'jbr\bin') + ';' + (Join-Path $DevEcoHome 'tools\node') + ';' + $env:PATH)
}
$taskOriginalEnvironment = @{}
foreach ($taskEnvironmentName in $taskBuildEnvironment.Keys) {
  $taskOriginalEnvironment[$taskEnvironmentName] = [Environment]::GetEnvironmentVariable($taskEnvironmentName, 'Process')
  [Environment]::SetEnvironmentVariable($taskEnvironmentName, $taskBuildEnvironment[$taskEnvironmentName], 'Process')
}

Push-Location $taskProjectRoot
try {
  # Reuse the complete toolchain shipped with DevEco; no package download is needed.
  $taskScopeDirectory = Join-Path $taskProjectRoot 'node_modules\@ohos'
  New-Item -ItemType Directory -Path $taskScopeDirectory -Force | Out-Null
  foreach ($taskPackage in @('hvigor', 'hvigor-ohos-plugin')) {
    $taskPackageLink = Join-Path $taskScopeDirectory $taskPackage
    if (-not (Test-Path -LiteralPath $taskPackageLink)) {
      New-Item -ItemType Junction -Path $taskPackageLink -Target (Join-Path $DevEcoHome "tools\hvigor\$taskPackage") | Out-Null
    }
  }
  if ($BuildArtifact -eq 'app') {
    # APP Pack is a project-level artifact. A valid release signing configuration
    # is still required before treating the resulting file as a submission pack.
    & $taskNode $taskWrapper --mode project -p 'product=default' -p "buildMode=$BuildMode" assembleApp --no-daemon --stacktrace
  } else {
    & $taskNode $taskWrapper --mode module -p 'product=default' -p 'module=entry@default' -p "buildMode=$BuildMode" assembleHap --no-daemon --stacktrace
  }
  if ($LASTEXITCODE -ne 0) {
    throw "Hvigor build failed with exit code $LASTEXITCODE."
  }
} finally {
  Pop-Location
  foreach ($taskEnvironmentName in $taskOriginalEnvironment.Keys) {
    [Environment]::SetEnvironmentVariable($taskEnvironmentName, $taskOriginalEnvironment[$taskEnvironmentName], 'Process')
  }
}
