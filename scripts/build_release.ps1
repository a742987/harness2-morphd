[CmdletBinding()]
param(
    [switch]$SkipTests,
    [string]$ElectronMirror = "",
    [string]$BuilderMirror = ""
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\hash_utils.ps1"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$npm = "npm.cmd"
$version = (Get-Content (Join-Path $root "package.json") -Raw | ConvertFrom-Json).version
$release = Join-Path $root "release"

if (-not $SkipTests) {
    & $npm run verify
    if ($LASTEXITCODE -ne 0) { throw "verify failed" }
}

if (Test-Path $release) {
    Remove-Item $release -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $release | Out-Null

Write-Host "== build web UI =="
& $npm --prefix consumer/web run build
if ($LASTEXITCODE -ne 0) { throw "web build failed" }

Write-Host "== build Windows engine =="
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root "scripts\build_engine.ps1")
if ($LASTEXITCODE -ne 0) { throw "engine build failed" }

Write-Host "== assemble platform package =="
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root "scripts\build_windows_package.ps1")
if ($LASTEXITCODE -ne 0) { throw "platform package assembly failed" }

Write-Host "== pack npm packages =="
& $npm --prefix packages/cli run build
if ($LASTEXITCODE -ne 0) { throw "CLI build failed" }
foreach ($relative in @("packages\cli", "packages\tray", "packages\platform\win32-x64")) {
    Push-Location (Join-Path $root $relative)
    try {
        & $npm pack --pack-destination $release
        if ($LASTEXITCODE -ne 0) { throw "npm pack failed in $relative" }
    } finally {
        Pop-Location
    }
}

Write-Host "== build Windows installers (unsigned) =="
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root "scripts\build_electron.ps1") `
    -ElectronMirror $ElectronMirror -BuilderMirror $BuilderMirror
if ($LASTEXITCODE -ne 0) { throw "Electron build failed" }

$electronRelease = Join-Path $root "consumer\electron\release"
$electronArtifacts = Join-Path $release "electron"
New-Item -ItemType Directory -Force -Path $electronArtifacts | Out-Null
Get-ChildItem $electronRelease -File -Filter "*.exe" | Copy-Item -Destination $electronArtifacts -Force
Get-ChildItem $electronArtifacts -File -Filter "*.exe" | ForEach-Object {
    Write-Host "installer: $($_.FullName)"
}

Write-Host "== checksums =="
$lines = @()
Get-ChildItem $release -Recurse -File | Where-Object { $_.Name -ne "SHA256SUMS-windows.txt" } | ForEach-Object {
    $relative = $_.FullName.Substring($release.Length + 1) -replace '\\', '/'
    $hash = Get-Sha256Hex -Path $_.FullName
    $lines += "$hash  $relative"
}
[System.IO.File]::WriteAllLines(
    (Join-Path $release "SHA256SUMS-windows.txt"),
    $lines,
    [System.Text.Encoding]::ASCII
)

Write-Host ""
Write-Host "release artifacts for $version are in $release"
