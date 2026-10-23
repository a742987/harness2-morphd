[CmdletBinding()]
param(
    [string]$Commit = "1cfe254e8d25"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dest = Join-Path $root "vendor\harness2-src"

if (Test-Path (Join-Path $dest "harness2\core\schedule.py")) {
    Write-Host "upstream already present at $dest"
    exit 0
}

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("harness2-" + [guid]::NewGuid().ToString("N"))
try {
    & git clone --filter=blob:none --no-checkout https://github.com/google-research/harness2 "$tmp\repo"
    if ($LASTEXITCODE -ne 0) { throw "git clone failed" }
    & git -C "$tmp\repo" checkout $Commit
    if ($LASTEXITCODE -ne 0) { throw "git checkout failed" }

    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Copy-Item (Join-Path $tmp "repo\harness2") (Join-Path $dest "harness2") -Recurse -Force
    foreach ($name in @("LICENSE", "README.md", "pyproject.toml")) {
        Copy-Item (Join-Path $tmp "repo\$name") (Join-Path $dest $name) -Force
    }
    $provenance = "Source: google-research/harness2`nCommit: $Commit`n"
    [System.IO.File]::WriteAllText((Join-Path $dest "PROVENANCE.md"), $provenance, [System.Text.Encoding]::UTF8)
    Write-Host "Pinned upstream copied to $dest; regenerate MANIFEST.sha256 before verifying."
} finally {
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
}
