[CmdletBinding()]
param(
    [switch]$SkipTests,
    [string]$ElectronMirror = "",
    [string]$BuilderMirror = ""
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\hash_utils.ps1"
. "$PSScriptRoot\release_utils.ps1"
. "$PSScriptRoot\sign_utils.ps1"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$npm = "npm.cmd"
$version = (Get-Content (Join-Path $root "package.json") -Raw | ConvertFrom-Json).version
$release = Join-Path $root "release"

foreach ($relative in @(
    "consumer\web\package.json",
    "consumer\electron\package.json",
    "packages\cli\package.json",
    "packages\tray\package.json",
    "packages\platform\win32-x64\package.json"
)) {
    $manifest = Get-Content (Join-Path $root $relative) -Raw | ConvertFrom-Json
    if ($manifest.version -ne $version) {
        throw "Version mismatch: $relative is $($manifest.version), workspace is $version."
    }
}
$pyproject = Get-Content (Join-Path $root "pyproject.toml") -Raw
if ($pyproject -notmatch '(?m)^version = "([^"]+)"' -or $Matches[1] -ne $version) {
    throw "Version mismatch: pyproject.toml does not declare $version."
}
$consumerInit = Get-Content (Join-Path $root "morphd\consumer\__init__.py") -Raw
if ($consumerInit -notmatch 'APP_VERSION = "([^"]+)"' -or $Matches[1] -ne $version) {
    throw "Version mismatch: morphd/consumer/__init__.py does not declare $version."
}

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

Write-Host "== build Windows installers =="
$electronArgs = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-File", (Join-Path $root "scripts\build_electron.ps1")
)
if ($ElectronMirror) { $electronArgs += @("-ElectronMirror", $ElectronMirror) }
if ($BuilderMirror) { $electronArgs += @("-BuilderMirror", $BuilderMirror) }
& powershell @electronArgs
if ($LASTEXITCODE -ne 0) { throw "Electron build failed" }

$electronRelease = Join-Path $root "consumer\electron\release"
$electronArtifacts = Join-Path $release "electron"
New-Item -ItemType Directory -Force -Path $electronArtifacts | Out-Null
Get-ChildItem $electronRelease -File -Filter "*.exe" | Copy-Item -Destination $electronArtifacts -Force
$installerFiles = @(Get-ChildItem $electronArtifacts -File -Filter "*.exe")
if ($installerFiles.Count -lt 2) {
    throw "Expected NSIS and portable installers, found $($installerFiles.Count) in $electronArtifacts."
}
$installerFiles | ForEach-Object {
    Write-Host "installer: $($_.FullName)"
}

Write-Host "== SBOM =="
$python = Resolve-PythonInterpreter -Root $root
$sbomArgs = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-File", (Join-Path $root "scripts\generate_sbom.ps1"),
    "-OutFile", (Join-Path $release "sbom.cdx.json"),
    "-ReleaseDir", $release
)
if ($python) { $sbomArgs += @("-Python", $python) }
& powershell @sbomArgs
if ($LASTEXITCODE -ne 0) { throw "SBOM generation failed" }

Write-Host "== signing status =="
Write-SigningStatus -ReleaseDir $release -EnginePath (Join-Path $root "dist\morphd-engine.exe") | Out-Null

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
