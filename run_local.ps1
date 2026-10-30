[CmdletBinding()]
param(
    [ValidateSet('web', 'desktop')]
    [string]$Mode = 'web',

    [string]$PythonPath = 'C:\Users\MK2SG\miniconda3\envs\PythonProject4\python.exe',

    [ValidateRange(1024, 65535)]
    [int]$Port = 8000,

    [switch]$Check
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot

if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Python interpreter was not found: $PythonPath"
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'main.py') -PathType Leaf)) {
    throw 'Run this script from a complete MindSpine source checkout.'
}

if ($Mode -eq 'desktop') {
    $pythonArguments = @((Join-Path $projectRoot 'launcher.py'))
    Write-Host 'Desktop mode: Edge WebView window with a loopback-only HTTP service.'
} else {
    $pythonArguments = @('-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', [string]$Port, '--workers', '1')
    Write-Host "Web mode: http://127.0.0.1:$Port/"
}

Write-Host "Python: $PythonPath"
Write-Host 'Existing process environment and runtime data are preserved. No dependencies are installed.'
if ($Check) {
    Write-Host 'Check completed. The application was not started.'
    return
}

$applicationExitCode = 1
Push-Location -LiteralPath $projectRoot
try {
    & $PythonPath @pythonArguments
    $applicationExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $applicationExitCode
