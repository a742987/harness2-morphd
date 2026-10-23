[CmdletBinding()]
param(
    [string]$Engine = "dist\morphd-engine.exe"
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\hash_utils.ps1"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$packageRoot = Join-Path $root "packages\platform\win32-x64"
$manifest = Get-Content (Join-Path $packageRoot "package.json") -Raw | ConvertFrom-Json
$rootManifest = Get-Content (Join-Path $root "package.json") -Raw | ConvertFrom-Json
if ($manifest.version -ne $rootManifest.version) {
    throw "Platform package version $($manifest.version) does not match workspace $($rootManifest.version)"
}

$enginePath = Join-Path $root $Engine
if (-not (Test-Path $enginePath)) {
    throw "Engine binary was not found at $enginePath. Run scripts\build_engine.ps1 first."
}

$binDir = Join-Path $packageRoot "bin"
New-Item -ItemType Directory -Force -Path $binDir | Out-Null
$target = Join-Path $binDir "morphd-engine.exe"
Copy-Item $enginePath $target -Force

$hash = Get-Sha256Hex -Path $target
$sums = "$hash  bin/morphd-engine.exe`n"
[System.IO.File]::WriteAllText((Join-Path $packageRoot "SHA256SUMS"), $sums, [System.Text.Encoding]::ASCII)

$size = [math]::Round((Get-Item $target).Length / 1MB, 1)
Write-Host "platform package: $target ($size MB)"
Write-Host "SHA256SUMS: $hash"
