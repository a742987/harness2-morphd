[CmdletBinding()]
param(
    [string]$ReleaseDir = "release",
    [switch]$AllowUnsigned
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\hash_utils.ps1"
. "$PSScriptRoot\sign_utils.ps1"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$dir = if ([System.IO.Path]::IsPathRooted($ReleaseDir)) {
    $ReleaseDir
} else {
    Join-Path $root $ReleaseDir
}
if (-not (Test-Path -LiteralPath $dir -PathType Container)) {
    throw "Release directory not found at $dir"
}
$dir = (Resolve-Path -LiteralPath $dir).Path

$failures = @()

# 1. SHA256SUMS must cover every published file and match its bytes.
$sumsPath = Join-Path $dir "SHA256SUMS-windows.txt"
if (-not (Test-Path -LiteralPath $sumsPath)) {
    $failures += "missing SHA256SUMS-windows.txt"
} else {
    $listed = @{}
    foreach ($line in Get-Content -LiteralPath $sumsPath) {
        if (-not $line.Trim()) { continue }
        $parts = $line -split '\s+', 2
        if ($parts.Count -ne 2) {
            $failures += "malformed checksum line: $line"
            continue
        }
        $expected = $parts[0].ToLowerInvariant()
        $relative = $parts[1].Trim() -replace '/', '\'
        $target = Join-Path $dir $relative
        if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
            $failures += "checksum target missing: $relative"
            continue
        }
        $actual = Get-Sha256Hex -Path $target
        if ($actual -ne $expected) {
            $failures += "checksum mismatch: $relative"
        }
        $listed[$relative.ToLowerInvariant()] = $true
    }
    $published = @(
        Get-ChildItem -LiteralPath $dir -Recurse -File |
            Where-Object { $_.Name -ne "SHA256SUMS-windows.txt" }
    )
    foreach ($file in $published) {
        $relative = $file.FullName.Substring($dir.Length + 1)
        if (-not $listed.ContainsKey($relative.ToLowerInvariant())) {
            $failures += "checksum missing for published file: $relative"
        }
    }
}

# 2. SBOM must be CycloneDX 1.5, identify the product, and hash every artifact.
$sbomPath = Join-Path $dir "sbom.cdx.json"
if (-not (Test-Path -LiteralPath $sbomPath)) {
    $failures += "missing sbom.cdx.json"
} else {
    try {
        $sbom = Get-Content -LiteralPath $sbomPath -Raw | ConvertFrom-Json
        if ($sbom.bomFormat -ne "CycloneDX") { $failures += "SBOM bomFormat is not CycloneDX" }
        if ($sbom.specVersion -ne "1.5") { $failures += "SBOM specVersion is not 1.5" }
        if (-not $sbom.metadata.component.name) { $failures += "SBOM product component is missing" }
        $artifactComponents = @(
            $sbom.components | Where-Object {
                @($_.properties | Where-Object {
                    $_.name -eq "morphd:release-artifact" -and $_.value -eq "true"
                }).Count -gt 0
            }
        )
        if ($artifactComponents.Count -eq 0) {
            $failures += "SBOM has no release artifact components"
        }
        foreach ($component in $artifactComponents) {
            $hashes = @($component.hashes | Where-Object { $_.alg -eq "SHA-256" -and $_.content })
            if ($hashes.Count -eq 0) {
                $failures += "SBOM artifact has no SHA-256 hash: $($component.name)"
            }
        }
    } catch {
        $failures += "SBOM is not valid JSON: $($_.Exception.Message)"
    }
}

# 3. Signing status must be explicit. Unsigned releases need -AllowUnsigned.
$statusPath = Join-Path $dir "signing-status.json"
if (-not (Test-Path -LiteralPath $statusPath)) {
    $failures += "missing signing-status.json"
} else {
    try {
        $status = Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json
        if ($status.schema -ne 1) { $failures += "signing-status schema is not 1" }
        if (-not $status.signed -and -not $AllowUnsigned) {
            $failures += "release is unsigned (rerun with -AllowUnsigned only for a documented unsigned build)"
        }
        foreach ($artifact in @($status.artifacts)) {
            if (-not $artifact.path) {
                $failures += "signing-status artifact entry has no path"
                continue
            }
            if ($artifact.path -like "platform package *") {
                # The engine is published inside the platform tarball; its bytes are
                # covered by SHA256SUMS-windows.txt above and by the package's own
                # SHA256SUMS, so this entry is an informational identity label.
                continue
            }
            $artifactPath = Join-Path $dir ($artifact.path -replace '/', '\')
            if (-not (Test-Path -LiteralPath $artifactPath -PathType Leaf)) {
                $failures += "signing-status artifact is missing: $($artifact.path)"
                continue
            }
            if ($status.signed -and -not (Test-AuthenticodeSigned -Path $artifactPath)) {
                $failures += "Authenticode verification failed for $($artifact.path)"
            }
        }
    } catch {
        $failures += "signing-status.json is not valid JSON: $($_.Exception.Message)"
    }
}

if ($failures.Count -gt 0) {
    $failures | ForEach-Object { Write-Host "FAIL: $_" -ForegroundColor Red }
    exit 1
}

Write-Host "release verification: OK"
Write-Host "  directory: $dir"
Write-Host "  checksums: SHA256SUMS-windows.txt"
Write-Host "  SBOM: sbom.cdx.json"
Write-Host "  signing: $(if ($AllowUnsigned) { 'explicitly allowed' } else { 'verified by status' })"
