[CmdletBinding()]
param(
    [string]$Python = "",
    [string]$OutDir = "dist"
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\release_utils.ps1"
. "$PSScriptRoot\sign_utils.ps1"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$pythonPath = Resolve-PythonInterpreter -Root $root -Explicit $Python
if (-not $pythonPath) {
    throw "Python 3.12/3.13 was not found. Set MORPHD_PYTHON or create .venv-win."
}
$pythonVersion = Get-PythonVersionLabel -Interpreter $pythonPath
if (-not $pythonVersion -or $pythonVersion -notmatch '^3\.1[23]\.') {
    throw "Python 3.12/3.13 is required; found $pythonVersion at $pythonPath."
}

$webui = Join-Path $root "morphd\consumer\webui"
$schemas = Join-Path $root "morphd\schemas"
$baseHarnesses = Join-Path $root "morphd\base_harnesses"
$templates = Join-Path $root "morphd\templates"
$pricing = Join-Path $root "morphd\pricing.yaml"
$vendor = Join-Path $root "vendor\harness2-src\harness2"
$vendorLicense = Join-Path $root "vendor\harness2-src\LICENSE"
$entry = Join-Path $root "scripts\engine_entry.py"

foreach ($path in @($webui, $schemas, $baseHarnesses, $templates, $pricing, $vendor, $vendorLicense, $entry)) {
    if (-not (Test-Path $path)) {
        throw "Missing engine input: $path"
    }
}

New-Item -ItemType Directory -Force -Path (Join-Path $root $OutDir) | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $root "build\pyinstaller") | Out-Null

$arguments = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--name", "morphd-engine",
    "--distpath", (Join-Path $root $OutDir),
    "--workpath", (Join-Path $root "build\pyinstaller"),
    "--add-data", "$webui;morphd\consumer\webui",
    "--add-data", "$schemas;morphd\schemas",
    "--add-data", "$baseHarnesses;morphd\base_harnesses",
    "--add-data", "$templates;morphd\templates",
    "--add-data", "$pricing;morphd",
    "--add-data", "$vendor;harness2",
    "--add-data", "$vendorLicense;harness2",
    "--collect-submodules", "keyring",
    "--collect-submodules", "pystray",
    "--collect-submodules", "PIL",
    "--collect-data", "jsonschema",
    "--hidden-import", "zstandard",
    "--hidden-import", "cryptography",
    "--hidden-import", "morphd.byok_proposer",
    $entry
)

& $pythonPath @arguments
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$engine = Join-Path $root (Join-Path $OutDir "morphd-engine.exe")
if (-not (Test-Path $engine)) {
    throw "Engine was not produced at $engine"
}
Invoke-AuthenticodeSign -Path $engine -Description "harness2-morphd engine" | Out-Null

$size = [math]::Round((Get-Item $engine).Length / 1MB, 1)
Write-Host "engine: $engine ($size MB)"
