[CmdletBinding()]
param(
    [string]$ElectronMirror = "",
    [string]$BuilderMirror = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$engine = Join-Path $root "dist\morphd-engine.exe"
if (-not (Test-Path $engine)) {
    throw "Missing $engine. Run scripts\build_engine.ps1 first."
}
$resourceEngine = Join-Path $root "consumer\electron\resources\engine"
New-Item -ItemType Directory -Force -Path $resourceEngine | Out-Null
Copy-Item $engine (Join-Path $resourceEngine "morphd-engine.exe") -Force

$release = Join-Path $root "consumer\electron\release"
if (Test-Path $release) {
    Remove-Item $release -Recurse -Force
}

$env:CSC_IDENTITY_AUTO_DISCOVERY = "false"
if ($ElectronMirror) { $env:ELECTRON_MIRROR = $ElectronMirror }
if ($BuilderMirror) { $env:ELECTRON_BUILDER_BINARIES_MIRROR = $BuilderMirror }

Push-Location (Join-Path $root "consumer\electron")
try {
    & npm.cmd run dist -- --win --x64 --publish never
    if ($LASTEXITCODE -ne 0) { throw "electron-builder failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}

Get-ChildItem $release -File -Filter "*.exe" | ForEach-Object {
    $size = [math]::Round($_.Length / 1MB, 1)
    Write-Host "installer: $($_.FullName) ($size MB)"
}
