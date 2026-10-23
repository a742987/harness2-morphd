[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\hash_utils.ps1"
$root = Split-Path -Parent $PSScriptRoot
$vendor = Join-Path $root "vendor\harness2-src"
$manifest = Join-Path $vendor "MANIFEST.sha256"

if (-not (Test-Path $manifest)) {
    throw "Vendor manifest not found at $manifest"
}

$failed = @()
foreach ($line in Get-Content $manifest) {
    if (-not $line.Trim()) { continue }
    $parts = $line -split '\s+', 2
    if ($parts.Count -ne 2) {
        $failed += "malformed line: $line"
        continue
    }
    $expected = $parts[0].ToLowerInvariant()
    $relative = $parts[1].Trim()
    if ($relative.StartsWith("./")) { $relative = $relative.Substring(2) }
    $path = Join-Path $vendor ($relative -replace '/', '\')
    if (-not (Test-Path $path)) {
        $failed += "missing: $relative"
        continue
    }
    $actual = Get-Sha256Hex -Path $path
    if ($actual -ne $expected) {
        $failed += "mismatch: $relative"
    }
}

if ($failed.Count -gt 0) {
    $failed | ForEach-Object { Write-Host $_ -ForegroundColor Red }
    exit 1
}
Write-Host "vendor integrity: OK"
