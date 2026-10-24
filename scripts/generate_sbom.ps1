#requires -version 5.1
<#
.SYNOPSIS
    Generate a deterministic CycloneDX 1.5 JSON SBOM for harness2-morphd.

.DESCRIPTION
    The SBOM contains:
      * the product component (name/version/license);
      * the Python runtime, declared pyproject dependencies and, when the
        interpreter is available, installed Python distributions;
      * npm workspace packages and their production dependency closure from
        package-lock.json (package.json ranges are the fallback);
      * release artifacts present in -ReleaseDir (engine .exe, npm tarballs,
        Electron .exe and similar) with SHA-256 hashes.

    Output ordering is stable. metadata.timestamp is omitted unless -Timestamp
    is passed explicitly, so repeated runs over the same inputs are byte-for-
    byte stable. The serial number is derived from the emitted component set.

.PARAMETER OutFile
    Destination path for the CycloneDX JSON file.

.PARAMETER ReleaseDir
    Directory scanned for release artifacts. Defaults to <root>\release when
    it exists; pass a missing/empty directory to emit an artifact-free SBOM.

.PARAMETER ArtifactsOnly
    Skip Python/npm dependency discovery and emit only the product component
    plus release artifacts. Useful before a build or in constrained CI.

.PARAMETER Timestamp
    Optional ISO-8601 timestamp recorded in metadata.timestamp. Omit it for a
    fully deterministic SBOM.

.PARAMETER Python
    Optional Python interpreter path used for the metadata probe.

.PARAMETER Version
    Override the product version (defaults to package.json).

.PARAMETER Root
    Repository root (defaults to the parent of scripts\).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$OutFile,
    [string]$ReleaseDir = "",
    [switch]$ArtifactsOnly,
    [string]$Timestamp = "",
    [string]$Python = "",
    [string]$Version = "",
    [string]$Root = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "hash_utils.ps1")
. (Join-Path $PSScriptRoot "release_utils.ps1")

if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
$Root = (Resolve-Path -LiteralPath $Root).Path

$outFull = if ([System.IO.Path]::IsPathRooted($OutFile)) {
    $OutFile
} else {
    Join-Path $Root $OutFile
}
$outFull = [System.IO.Path]::GetFullPath($outFull)
$outParent = Split-Path -Parent $outFull
if ($outParent -and -not (Test-Path -LiteralPath $outParent)) {
    New-Item -ItemType Directory -Force -Path $outParent | Out-Null
}

if (-not $Version) {
    $packageJsonPath = Join-Path $Root "package.json"
    if (-not (Test-Path -LiteralPath $packageJsonPath)) {
        throw "package.json was not found at $packageJsonPath"
    }
    $Version = (Get-Content -LiteralPath $packageJsonPath -Raw | ConvertFrom-Json).version
}
if (-not $Version) { throw "Product version could not be determined." }

if ($ReleaseDir) {
    $releaseFull = if ([System.IO.Path]::IsPathRooted($ReleaseDir)) {
        $ReleaseDir
    } else {
        Join-Path $Root $ReleaseDir
    }
} else {
    $releaseFull = Join-Path $Root "release"
}

$normalizedTimestamp = $null
if ($Timestamp) { $normalizedTimestamp = ConvertTo-UtcTimestamp -Value $Timestamp }

$productRef = "pkg:generic/harness2-morphd@$Version"
$components = New-Object System.Collections.ArrayList
$componentIndex = @{}

function Add-BomComponent {
    param([Parameter(Mandatory = $true)]$Component)

    $key = [string]$Component["bom-ref"]
    if (-not $key) { throw "Component is missing bom-ref." }
    if ($componentIndex.ContainsKey($key)) {
        $existing = $componentIndex[$key]
        if (-not $existing["hashes"] -and $Component["hashes"]) {
            $existing["hashes"] = $Component["hashes"]
        }
        return
    }
    $componentIndex[$key] = $Component
    [void]$components.Add($Component)
}

function Get-SortedUnique {
    param([object[]]$Values = @())

    $seen = @{}
    $result = @()
    foreach ($value in $Values) {
        if ($null -eq $value -or [string]::IsNullOrWhiteSpace([string]$value)) { continue }
        $text = [string]$value
        if (-not $seen.ContainsKey($text)) {
            $seen[$text] = $true
            $result += $text
        }
    }
    return ,@($result | Sort-Object)
}

function Get-LicenseEntries {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) { return @() }
    $license = ($Value.Trim() -replace '\s+', ' ')
    if ($license.Length -gt 120 -or $license -match '[\r\n]') { return @() }
    if ($license -match '^[A-Za-z0-9][A-Za-z0-9.+-]*$') {
        return @([ordered]@{ license = [ordered]@{ id = $license } })
    }
    if ($license -match '^[A-Za-z0-9().+\- ]+$') {
        return @([ordered]@{ expression = $license })
    }
    return @()
}

function Get-PypiPurl {
    param([string]$Name, [string]$PackageVersion)

    $normalized = ($Name.ToLowerInvariant() -replace '[-_.]+', '-')
    $purl = "pkg:pypi/" + [System.Uri]::EscapeDataString($normalized)
    if ($PackageVersion) {
        $purl += "@" + [System.Uri]::EscapeDataString($PackageVersion)
    }
    return $purl
}

function Get-NpmPurl {
    param([string]$Name, [string]$PackageVersion)

    $encodedName = $Name.Replace("@", "%40")
    $purl = "pkg:npm/$encodedName"
    if ($PackageVersion -and $PackageVersion -match '^\d') {
        $purl += "@" + [System.Uri]::EscapeDataString($PackageVersion)
    }
    return $purl
}

function Convert-SriToHash {
    param([string]$Integrity)

    if ([string]::IsNullOrWhiteSpace($Integrity)) { return $null }
    $token = ($Integrity.Trim() -split '\s+')[0]
    if ($token -notmatch '^(sha512|sha256|sha1)-(.+)$') { return $null }
    $algorithm = switch ($matches[1]) {
        "sha512" { "SHA-512" }
        "sha256" { "SHA-256" }
        "sha1" { "SHA-1" }
        default { $null }
    }
    if (-not $algorithm) { return $null }
    try {
        $bytes = [System.Convert]::FromBase64String($matches[2])
    } catch {
        return $null
    }
    $hex = [System.BitConverter]::ToString($bytes) -replace '-', ''
    return [ordered]@{ alg = $algorithm; content = $hex.ToLowerInvariant() }
}

function Get-PackageLockEntry {
    param($LockPackages, [string]$Key)

    if ($null -eq $LockPackages) { return $null }
    $property = $LockPackages.PSObject.Properties |
        Where-Object { $_.Name -eq $Key } |
        Select-Object -First 1
    if ($property) { return $property.Value }
    return $null
}

# ---------------------------------------------------------------------------
# Product component
# ---------------------------------------------------------------------------
$productComponent = [ordered]@{
    type      = "application"
    "bom-ref" = $productRef
    name      = "harness2-morphd"
    version   = $Version
    purl      = $productRef
    licenses  = @([ordered]@{ license = [ordered]@{ id = "Apache-2.0" } })
    properties = @(
        [ordered]@{ name = "morphd:platform"; value = "windows-x64" },
        [ordered]@{ name = "morphd:os"; value = "Windows 10/11 x64" }
    )
}

# Bundled Electron runtime (a devDependency of the desktop package, but shipped).
$electronVersion = ""
$electronLock = Join-Path $Root "consumer\electron\package-lock.json"
if (Test-Path -LiteralPath $electronLock) {
    try {
        $lockText = (Get-Content -LiteralPath $electronLock -Raw) -replace '"":', '"__morphd_lock_root__":'
        $electronLockData = $lockText | ConvertFrom-Json
        $electronEntry = Get-PackageLockEntry -LockPackages $electronLockData.packages -Key "node_modules/electron"
        if ($electronEntry -and $electronEntry.version) {
            $electronVersion = [string]$electronEntry.version
        }
    } catch {
        $electronVersion = ""
    }
}
if ($electronVersion) {
    $electronPurl = Get-NpmPurl -Name "electron" -PackageVersion $electronVersion
    Add-BomComponent ([ordered]@{
        type       = "library"
        "bom-ref"  = $electronPurl
        name       = "electron"
        version    = $electronVersion
        purl       = $electronPurl
        scope      = "required"
        licenses   = @([ordered]@{ license = [ordered]@{ id = "MIT" } })
        properties = @(
            [ordered]@{ name = "morphd:bundled-runtime"; value = "electron" },
            [ordered]@{ name = "morphd:license-file"; value = "LICENSES.chromium.html" }
        )
    })
}

# Vendored upstream Harness² source bundled into the engine.
$vendorRoot = Join-Path $Root "vendor\harness2-src"
$vendorPyproject = Join-Path $vendorRoot "pyproject.toml"
$vendorProvenance = Join-Path $vendorRoot "PROVENANCE.md"
if ((Test-Path -LiteralPath $vendorPyproject) -and (Test-Path -LiteralPath $vendorProvenance)) {
    $vendorVersion = ""
    $vendorCommit = ""
    $vendorProjectText = Get-Content -LiteralPath $vendorPyproject -Raw
    if ($vendorProjectText -match '(?m)^version = "([^"]+)"') { $vendorVersion = $Matches[1] }
    $vendorProvenanceText = Get-Content -LiteralPath $vendorProvenance -Raw
    if ($vendorProvenanceText -match 'Commit: `([0-9a-f]+)`') { $vendorCommit = $Matches[1] }
    if ($vendorCommit) {
        $vendorPurl = "pkg:github/google-research/harness2@$vendorCommit"
        $vendorComponent = [ordered]@{
            type       = "library"
            "bom-ref"  = $vendorPurl
            name       = "harness2"
            purl       = $vendorPurl
            scope      = "required"
            licenses   = @([ordered]@{ license = [ordered]@{ id = "Apache-2.0" } })
            properties = @(
                [ordered]@{ name = "morphd:bundled-source"; value = "vendor/harness2-src" }
            )
        }
        if ($vendorVersion) { $vendorComponent["version"] = $vendorVersion }
        Add-BomComponent $vendorComponent
    }
}

# ---------------------------------------------------------------------------
# Python runtime and dependencies
# ---------------------------------------------------------------------------
$pythonRuntimeRef = $null
$pythonCoreRefs = New-Object System.Collections.ArrayList

if (-not $ArtifactsOnly) {
    $pyprojectPath = Join-Path $Root "pyproject.toml"
    $probePath = Join-Path $PSScriptRoot "sbom_python_probe.py"
    $interpreter = $null
    $probe = $null

    if (Test-Path -LiteralPath $pyprojectPath) {
        $interpreter = Resolve-PythonInterpreter -Root $Root -Explicit $Python
        if ($interpreter -and (Test-Path -LiteralPath $probePath)) {
            $raw = $null
            $exitCode = 1
            $previousUtf8 = $env:PYTHONUTF8
            try {
                $env:PYTHONUTF8 = "1"
                $raw = & $interpreter $probePath --pyproject $pyprojectPath 2>$null
                $exitCode = $LASTEXITCODE
            } catch {
                $raw = $null
            } finally {
                $env:PYTHONUTF8 = $previousUtf8
            }
            if ($exitCode -eq 0 -and $raw) {
                try {
                    $candidate = (($raw | Out-String).Trim()) | ConvertFrom-Json
                    if ($candidate.ok) { $probe = $candidate }
                } catch {
                    $probe = $null
                }
            }
        }
    }

    if ($probe) {
        $pythonVersion = [string]$probe.python.version
        if ($pythonVersion) {
            $pythonRuntimeRef = "pkg:generic/python@$pythonVersion"
            $runtimeProperties = @(
                [ordered]@{ name = "morphd:runtime"; value = "python" }
            )
            if ($probe.python.implementation) {
                $runtimeProperties += [ordered]@{ name = "morphd:implementation"; value = [string]$probe.python.implementation }
            }
            if ($probe.python.requiresPython) {
                $runtimeProperties += [ordered]@{ name = "morphd:requires-python"; value = [string]$probe.python.requiresPython }
            }
            Add-BomComponent ([ordered]@{
                type       = "platform"
                "bom-ref"  = $pythonRuntimeRef
                name       = "python"
                version    = $pythonVersion
                purl       = $pythonRuntimeRef
                properties = $runtimeProperties
            })
        }

        foreach ($dependency in @($probe.dependencies)) {
            if (-not $dependency -or -not $dependency.name) { continue }
            $dependencyName = [string]$dependency.name
            $dependencyVersion = [string]$dependency.installedVersion
            $purl = Get-PypiPurl -Name $dependencyName -PackageVersion $dependencyVersion
            $properties = @(
                [ordered]@{ name = "morphd:python-group"; value = [string]$dependency.group },
                [ordered]@{ name = "morphd:requirement"; value = [string]$dependency.requirement }
            )
            $component = [ordered]@{
                type       = "library"
                "bom-ref"  = $purl
                name       = $dependencyName
                purl       = $purl
                scope      = if ([string]$dependency.scope -eq "optional") { "optional" } else { "required" }
                properties = $properties
            }
            if ($dependencyVersion) { $component["version"] = $dependencyVersion }
            Add-BomComponent $component
            if ([string]$dependency.scope -ne "optional" -and $dependencyVersion) {
                [void]$pythonCoreRefs.Add($purl)
            }
        }

        foreach ($package in @($probe.installed)) {
            if (-not $package -or -not $package.name -or -not $package.version) { continue }
            $packageName = [string]$package.name
            $packageVersion = [string]$package.version
            $purl = Get-PypiPurl -Name $packageName -PackageVersion $packageVersion
            $component = [ordered]@{
                type       = "library"
                "bom-ref"  = $purl
                name       = $packageName
                version    = $packageVersion
                purl       = $purl
                properties = @(
                    [ordered]@{ name = "morphd:python-evidence"; value = "installed-environment" }
                )
            }
            $licenseEntries = Get-LicenseEntries -Value ([string]$package.license)
            if ($licenseEntries.Count -gt 0) { $component["licenses"] = $licenseEntries }
            Add-BomComponent $component
        }
    } elseif (Test-Path -LiteralPath $pyprojectPath) {
        # Fallback: parse the [project] table textually and ask Python for its
        # version if possible. This keeps the SBOM useful without pip metadata.
        $pyprojectText = Get-Content -LiteralPath $pyprojectPath -Raw
        $pythonVersion = $null
        if (-not $interpreter) {
            $interpreter = Resolve-PythonInterpreter -Root $Root -Explicit $Python
        }
        if ($interpreter) { $pythonVersion = Get-PythonVersionLabel -Interpreter $interpreter }

        if ($pythonVersion) {
            $pythonRuntimeRef = "pkg:generic/python@$pythonVersion"
            Add-BomComponent ([ordered]@{
                type       = "platform"
                "bom-ref"  = $pythonRuntimeRef
                name       = "python"
                version    = $pythonVersion
                purl       = $pythonRuntimeRef
                properties = @([ordered]@{ name = "morphd:runtime"; value = "python" })
            })
        }

        $dependencyBlock = [regex]::Match(
            $pyprojectText,
            '(?ms)^\s*dependencies\s*=\s*\[(.*?)\]'
        )
        if ($dependencyBlock.Success) {
            $requirements = [regex]::Matches(
                $dependencyBlock.Groups[1].Value,
                '"([^"]+)"|''([^'']+)'''
            )
            foreach ($match in $requirements) {
                $requirement = if ($match.Groups[1].Success) { $match.Groups[1].Value } else { $match.Groups[2].Value }
                $nameMatch = [regex]::Match($requirement, '^\s*([A-Za-z0-9][A-Za-z0-9._-]*)')
                if (-not $nameMatch.Success) { continue }
                $dependencyName = $nameMatch.Groups[1].Value
                $purl = Get-PypiPurl -Name $dependencyName -PackageVersion $null
                Add-BomComponent ([ordered]@{
                    type       = "library"
                    "bom-ref"  = $purl
                    name       = $dependencyName
                    purl       = $purl
                    scope      = "required"
                    properties = @(
                        [ordered]@{ name = "morphd:python-group"; value = "project" },
                        [ordered]@{ name = "morphd:requirement"; value = $requirement }
                    )
                })
                [void]$pythonCoreRefs.Add($purl)
            }
        }
    }
}

# ---------------------------------------------------------------------------
# npm workspace packages and production dependency closure
# ---------------------------------------------------------------------------
$workspaceRefs = New-Object System.Collections.ArrayList
$workspaceByName = @{}
$workspaceDependencies = @{}

if (-not $ArtifactsOnly) {
    $manifestFiles = @()
    $seenManifests = @{}
    foreach ($pattern in @("packages\*\package.json", "packages\platform\*\package.json", "consumer\*\package.json")) {
        $matchPath = Join-Path $Root $pattern
        foreach ($item in @(Get-Item -Path $matchPath -ErrorAction SilentlyContinue)) {
            if ($item.PSIsContainer) { continue }
            if (-not $seenManifests.ContainsKey($item.FullName)) {
                $seenManifests[$item.FullName] = $true
                $manifestFiles += $item
            }
        }
    }
    $manifestFiles = @($manifestFiles | Sort-Object FullName)

    # First pass: register every workspace name before resolving dependencies,
    # because the sort order is not the dependency order.
    $workspaceInputs = @()
    foreach ($manifestFile in $manifestFiles) {
        $packageJson = Get-Content -LiteralPath $manifestFile.FullName -Raw | ConvertFrom-Json
        if (-not $packageJson.name) { continue }
        $directory = Split-Path -Parent $manifestFile.FullName
        $workspaceInputs += [pscustomobject]@{
            File        = $manifestFile
            Directory   = $directory
            PackageJson = $packageJson
        }
        $workspaceByName[[string]$packageJson.name] = @{
            Ref     = (Get-NpmPurl -Name ([string]$packageJson.name) -PackageVersion ([string]$packageJson.version))
            Version = [string]$packageJson.version
        }
    }

    foreach ($workspaceInput in $workspaceInputs) {
        $packageJson = $workspaceInput.PackageJson
        $directory = $workspaceInput.Directory
        $packageName = [string]$packageJson.name
        $packageVersion = [string]$packageJson.version
        $purl = Get-NpmPurl -Name $packageName -PackageVersion $packageVersion
        $properties = @(
            [ordered]@{ name = "morphd:npm-workspace-path"; value = (Get-RelativePosixPath -BaseDir $Root -Path $directory) }
        )
        if ($packageJson.private -eq $true) {
            $properties += [ordered]@{ name = "morphd:private"; value = "true" }
        }
        $component = [ordered]@{
            type       = if ($null -ne $packageJson.bin) { "application" } else { "library" }
            "bom-ref"  = $purl
            name       = $packageName
            purl       = $purl
            properties = $properties
        }
        if ($packageVersion) { $component["version"] = $packageVersion }
        $licenseEntries = Get-LicenseEntries -Value ([string]$packageJson.license)
        if ($licenseEntries.Count -gt 0) { $component["licenses"] = $licenseEntries }
        Add-BomComponent $component
        [void]$workspaceRefs.Add($purl)

        $dependencyRanges = [ordered]@{}
        foreach ($section in @("dependencies", "optionalDependencies")) {
            $sectionValue = $packageJson.$section
            if ($null -eq $sectionValue) { continue }
            foreach ($property in $sectionValue.PSObject.Properties) {
                if (-not $dependencyRanges.Contains($property.Name)) {
                    $dependencyRanges[$property.Name] = [string]$property.Value
                }
            }
        }

        $lockPath = Join-Path $directory "package-lock.json"
        $lock = $null
        if (Test-Path -LiteralPath $lockPath) {
            try {
                # Windows PowerShell 5.1 cannot materialize an empty JSON key.
                # npm lock v3 uses "" for the root package, so rename every
                # empty object key before parsing; the root entry is skipped.
                $lockText = (Get-Content -LiteralPath $lockPath -Raw) -replace '"":', '"__morphd_lock_root__":'
                $lock = $lockText | ConvertFrom-Json
            } catch {
                $lock = $null
            }
            if ($lock -and $lock.packages) {
                $lockRelative = Get-RelativePosixPath -BaseDir $Root -Path $lockPath
                $entries = @($lock.packages.PSObject.Properties | Sort-Object Name)
                foreach ($entryProperty in $entries) {
                    if ($entryProperty.Name -eq "" -or $entryProperty.Name -eq "__morphd_lock_root__") { continue }
                    $entry = $entryProperty.Value
                    if ($entry.dev -eq $true) { continue }
                    if ($entry.link -eq $true) { continue }

                    $entryName = $entry.name
                    if (-not $entryName) {
                        $entryName = ($entryProperty.Name -split 'node_modules/')[-1]
                    }
                    $entryVersion = [string]$entry.version
                    if (-not $entryName -or -not $entryVersion) { continue }

                    $entryPurl = Get-NpmPurl -Name ([string]$entryName) -PackageVersion $entryVersion
                    $entryComponent = [ordered]@{
                        type       = "library"
                        "bom-ref"  = $entryPurl
                        name       = [string]$entryName
                        version    = $entryVersion
                        purl       = $entryPurl
                        properties = @(
                            [ordered]@{ name = "morphd:npm-evidence"; value = $lockRelative }
                        )
                    }
                    $licenseEntries = Get-LicenseEntries -Value ([string]$entry.license)
                    if ($licenseEntries.Count -gt 0) { $entryComponent["licenses"] = $licenseEntries }
                    $hash = Convert-SriToHash -Integrity ([string]$entry.integrity)
                    if ($hash) { $entryComponent["hashes"] = @($hash) }
                    if ($entry.resolved -and ([string]$entry.resolved) -match '^https?://') {
                        $entryComponent["externalReferences"] = @(
                            [ordered]@{ type = "distribution"; url = [string]$entry.resolved }
                        )
                    }
                    Add-BomComponent $entryComponent
                }
            }
        }

        $directRefs = New-Object System.Collections.ArrayList
        foreach ($dependencyName in $dependencyRanges.Keys) {
            $range = [string]$dependencyRanges[$dependencyName]
            $resolvedVersion = $null
            $resolvedRef = $null

            if ($lock -and $lock.packages) {
                $lockEntry = Get-PackageLockEntry -LockPackages $lock.packages -Key ("node_modules/" + $dependencyName)
                if ($lockEntry -and $lockEntry.version) {
                    $resolvedVersion = [string]$lockEntry.version
                    $resolvedRef = Get-NpmPurl -Name $dependencyName -PackageVersion $resolvedVersion
                }
            }
            if (-not $resolvedRef -and $workspaceByName.ContainsKey($dependencyName)) {
                $resolvedRef = $workspaceByName[$dependencyName].Ref
                $resolvedVersion = $workspaceByName[$dependencyName].Version
            }
            if (-not $resolvedRef) {
                $resolvedRef = "urn:morphd:npm-unresolved:" + [System.Uri]::EscapeDataString($dependencyName)
                Add-BomComponent ([ordered]@{
                    type       = "library"
                    "bom-ref"  = $resolvedRef
                    name       = $dependencyName
                    version    = $range
                    properties = @(
                        [ordered]@{ name = "morphd:npm-declared-range"; value = $range },
                        [ordered]@{ name = "morphd:npm-evidence"; value = "package.json fallback" }
                    )
                })
            }
            if ($resolvedRef) { [void]$directRefs.Add($resolvedRef) }
        }
        $workspaceDependencies[$purl] = Get-SortedUnique -Values @($directRefs)
    }
}

# ---------------------------------------------------------------------------
# Release artifacts
# ---------------------------------------------------------------------------
$artifactRefs = New-Object System.Collections.ArrayList
$artifactExtensions = @{
    ".exe"   = "windows-executable"
    ".dll"   = "windows-library"
    ".msi"   = "windows-installer"
    ".msix"  = "windows-installer"
    ".nupkg" = "nuget-package"
    ".tgz"   = "npm-tarball"
    ".whl"   = "python-wheel"
    ".zip"   = "archive"
}
$artifactExcludedNames = @(
    "release-manifest.json",
    "SHA256SUMS-windows.txt",
    "signing-status.json"
)

if ($releaseFull -and (Test-Path -LiteralPath $releaseFull -PathType Container)) {
    $releaseFull = (Resolve-Path -LiteralPath $releaseFull).Path
    $artifactFiles = @(Get-ChildItem -LiteralPath $releaseFull -Recurse -File | Sort-Object FullName)
    foreach ($artifactFile in $artifactFiles) {
        if ($artifactFile.FullName -ieq $outFull) { continue }
        if ($artifactExcludedNames -contains $artifactFile.Name) { continue }
        $extension = $artifactFile.Extension.ToLowerInvariant()
        if (-not $artifactExtensions.ContainsKey($extension)) { continue }

        $relative = Get-RelativePosixPath -BaseDir $releaseFull -Path $artifactFile.FullName
        $kind = $artifactExtensions[$extension]
        $hash = Get-Sha256Hex -Path $artifactFile.FullName
        $artifactRef = "urn:morphd:artifact:" + [System.Uri]::EscapeDataString($relative)
        $properties = @(
            [ordered]@{ name = "morphd:release-artifact"; value = "true" },
            [ordered]@{ name = "morphd:artifact-path"; value = $relative },
            [ordered]@{ name = "morphd:artifact-kind"; value = $kind }
        )
        if ($kind -eq "windows-executable" -or $kind -eq "windows-library") {
            $properties += [ordered]@{ name = "morphd:binary"; value = "true" }
        }
        $artifactComponent = [ordered]@{
            type       = "file"
            "bom-ref"  = $artifactRef
            name       = $relative
            hashes     = @([ordered]@{ alg = "SHA-256"; content = $hash })
            properties = $properties
        }
        $mimeType = switch ($extension) {
            ".exe" { "application/vnd.microsoft.portable-executable" }
            ".dll" { "application/vnd.microsoft.portable-executable" }
            ".tgz" { "application/gzip" }
            ".whl" { "application/zip" }
            ".zip" { "application/zip" }
            ".msi" { "application/x-msi" }
            default { $null }
        }
        if ($mimeType) { $artifactComponent["mimeType"] = $mimeType }
        Add-BomComponent $artifactComponent
        [void]$artifactRefs.Add($artifactRef)
    }
}

# ---------------------------------------------------------------------------
# Component ordering, dependency graph and deterministic serial number
# ---------------------------------------------------------------------------
$sortedComponents = @(
    $components | Sort-Object { "$($_['type'])|$($_['bom-ref'])" }
)

$validRefs = @{}
$validRefs[$productRef] = $true
foreach ($component in $sortedComponents) { $validRefs[[string]$component["bom-ref"]] = $true }

$dependencies = New-Object System.Collections.ArrayList

$productDepends = New-Object System.Collections.ArrayList
foreach ($ref in @($workspaceRefs)) { if ($ref) { [void]$productDepends.Add($ref) } }
if ($pythonRuntimeRef) { [void]$productDepends.Add($pythonRuntimeRef) }
foreach ($ref in @($artifactRefs)) { if ($ref) { [void]$productDepends.Add($ref) } }
if (-not $pythonRuntimeRef) {
    foreach ($ref in @($pythonCoreRefs)) { if ($ref) { [void]$productDepends.Add($ref) } }
}
$productDepends = Get-SortedUnique -Values @($productDepends)
if ($productDepends.Count -gt 0) {
    [void]$dependencies.Add([ordered]@{ ref = $productRef; dependsOn = $productDepends })
}

if ($pythonRuntimeRef -and $pythonCoreRefs.Count -gt 0) {
    [void]$dependencies.Add([ordered]@{
        ref       = $pythonRuntimeRef
        dependsOn = (Get-SortedUnique -Values @($pythonCoreRefs))
    })
}

foreach ($workspaceRef in @($workspaceRefs)) {
    $direct = $workspaceDependencies[$workspaceRef]
    if ($direct -and $direct.Count -gt 0) {
        [void]$dependencies.Add([ordered]@{ ref = $workspaceRef; dependsOn = $direct })
    }
}
$dependencies = @($dependencies | Sort-Object { $_["ref"] })

$seedParts = New-Object System.Collections.ArrayList
foreach ($component in $sortedComponents) {
    $hashContent = ""
    if ($component["hashes"]) {
        $hashContent = (@($component["hashes"]) | ForEach-Object { $_["content"] }) -join ","
    }
    [void]$seedParts.Add("$($component['type'])|$($component['bom-ref'])|$hashContent")
}
$seed = "harness2-morphd|$Version|" + ($seedParts -join "`n")
$serialUuid = New-DeterministicUuid -Seed $seed

$metadata = [ordered]@{
    tools = [ordered]@{
        components = @(
            [ordered]@{
                type    = "application"
                name    = "generate_sbom.ps1"
                version = $Version
            }
        )
    }
    component = $productComponent
}
if ($normalizedTimestamp) { $metadata["timestamp"] = $normalizedTimestamp }

$document = [ordered]@{
    '$schema'      = "http://cyclonedx.org/schema/bom-1.5.schema.json"
    bomFormat      = "CycloneDX"
    specVersion    = "1.5"
    serialNumber   = "urn:uuid:$serialUuid"
    version        = 1
    metadata       = $metadata
    components     = $sortedComponents
    dependencies   = $dependencies
}

$json = $document | ConvertTo-Json -Depth 100
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($outFull, $json + "`n", $utf8NoBom)

$artifactCount = $artifactRefs.Count
Write-Host "SBOM: $outFull"
Write-Host "components: $($sortedComponents.Count), release artifacts: $artifactCount, spec: CycloneDX 1.5"
