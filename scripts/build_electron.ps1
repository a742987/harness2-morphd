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
$resourceBinary = Join-Path $resourceEngine "morphd-engine.exe"
Copy-Item $engine $resourceBinary -Force
$engineHash = (Get-FileHash -Path $resourceBinary -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -Path (Join-Path $resourceEngine "SHA256SUMS") -Value "$engineHash  morphd-engine.exe" -Encoding ascii

$release = Join-Path $root "consumer\electron\release"
if (Test-Path $release) {
    Remove-Item $release -Recurse -Force
}

$env:CSC_IDENTITY_AUTO_DISCOVERY = "false"
if ($env:MORPHD_SIGN_CERT) {
    # electron-builder reads CSC_LINK/CSC_KEY_PASSWORD and signs app + installers.
    $env:CSC_LINK = $env:MORPHD_SIGN_CERT
    if ($env:MORPHD_SIGN_PASSWORD) { $env:CSC_KEY_PASSWORD = $env:MORPHD_SIGN_PASSWORD }
    Write-Host "Authenticode signing enabled for Electron artifacts."
} else {
    Write-Warning "Electron artifacts will be unsigned (MORPHD_SIGN_CERT is not set)."
}
if ($ElectronMirror) { $env:ELECTRON_MIRROR = $ElectronMirror }
if ($BuilderMirror) { $env:ELECTRON_BUILDER_BINARIES_MIRROR = $BuilderMirror }

Push-Location (Join-Path $root "consumer\electron")
try {
    & npm.cmd run dist -- --win --x64 --publish never
    if ($LASTEXITCODE -ne 0) { throw "electron-builder failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}

$installers = @(Get-ChildItem $release -File -Filter "*.exe")
if ($installers.Count -lt 2) {
    throw "Expected NSIS and portable installers, found $($installers.Count) .exe files in $release."
}
if (-not ($installers | Where-Object { $_.Name -match 'Setup' })) {
    throw "The NSIS installer was not produced in $release."
}
if (-not ($installers | Where-Object { $_.Name -notmatch 'Setup' })) {
    throw "The portable installer was not produced in $release."
}
$installers | ForEach-Object {
    $size = [math]::Round($_.Length / 1MB, 1)
    Write-Host "installer: $($_.FullName) ($size MB)"
}
