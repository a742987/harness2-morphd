# Shared helpers for the release supply-chain scripts.
# Windows PowerShell 5.1 compatible.

function Get-RelativePosixPath {
    param(
        [Parameter(Mandatory = $true)][string]$BaseDir,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $base = [System.IO.Path]::GetFullPath($BaseDir).TrimEnd('\', '/')
    $full = [System.IO.Path]::GetFullPath($Path)
    $prefix = $base + [System.IO.Path]::DirectorySeparatorChar
    if (-not $full.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Path '$full' is not under '$base'."
    }
    return $full.Substring($prefix.Length).Replace('\', '/')
}

function ConvertTo-UtcTimestamp {
    param([Parameter(Mandatory = $true)][string]$Value)

    try {
        $parsed = [System.DateTimeOffset]::Parse(
            $Value,
            [System.Globalization.CultureInfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::AssumeUniversal
        )
    } catch {
        throw "Invalid timestamp '$Value'. Use an ISO-8601 value, for example 2026-10-24T12:00:00Z."
    }
    return $parsed.UtcDateTime.ToString(
        "yyyy-MM-ddTHH:mm:ssZ",
        [System.Globalization.CultureInfo]::InvariantCulture
    )
}

function Get-UtcNowTimestamp {
    return [System.DateTime]::UtcNow.ToString(
        "yyyy-MM-ddTHH:mm:ssZ",
        [System.Globalization.CultureInfo]::InvariantCulture
    )
}

function Resolve-PythonInterpreter {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [string]$Explicit = ""
    )

    $candidates = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($Explicit)) { $candidates.Add($Explicit) }
    if (-not [string]::IsNullOrWhiteSpace($env:MORPHD_PYTHON)) { $candidates.Add($env:MORPHD_PYTHON) }
    $candidates.Add((Join-Path $Root ".venv-win\Scripts\python.exe"))
    $candidates.Add((Join-Path $Root ".venv\Scripts\python.exe"))
    $candidates.Add("python")

    foreach ($candidate in $candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) { continue }
        $resolved = $null
        if ($candidate.Contains('\') -or $candidate.Contains('/') -or
            [System.IO.Path]::IsPathRooted($candidate)) {
            $path = $candidate
            if (-not [System.IO.Path]::IsPathRooted($path)) { $path = Join-Path $Root $path }
            if (Test-Path -LiteralPath $path -PathType Leaf) {
                $resolved = (Resolve-Path -LiteralPath $path).Path
            }
        } else {
            $command = Get-Command $candidate -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($command) { $resolved = $command.Source }
        }
        if ($resolved) { return $resolved }
    }
    return $null
}

function Get-PythonVersionLabel {
    param([Parameter(Mandatory = $true)][string]$Interpreter)

    try {
        $output = (& $Interpreter --version 2>&1 | Out-String).Trim()
    } catch {
        return $null
    }
    if ($output -match '(\d+\.\d+\.\d+(?:[A-Za-z0-9.+-]*)?)') {
        return $matches[1]
    }
    if ($output) { return $output }
    return $null
}

function New-DeterministicUuid {
    <#
        Deterministic RFC 4122 version-5 style UUID (SHA-1 over the DNS
        namespace plus a caller seed). Used only to give a stable CycloneDX
        serialNumber for identical inputs.
    #>
    param([Parameter(Mandatory = $true)][string]$Seed)

    $namespace = [System.Guid]::Parse("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
    $namespaceBytes = $namespace.ToByteArray()
    # .NET Guid mixes endianness; convert the first three fields to RFC order.
    [Array]::Reverse($namespaceBytes, 0, 4)
    [Array]::Reverse($namespaceBytes, 4, 2)
    [Array]::Reverse($namespaceBytes, 6, 2)

    $nameBytes = [System.Text.Encoding]::UTF8.GetBytes($Seed)
    $buffer = New-Object byte[] ($namespaceBytes.Length + $nameBytes.Length)
    [Array]::Copy($namespaceBytes, 0, $buffer, 0, $namespaceBytes.Length)
    [Array]::Copy($nameBytes, 0, $buffer, $namespaceBytes.Length, $nameBytes.Length)

    $sha1 = [System.Security.Cryptography.SHA1]::Create()
    try {
        $hash = $sha1.ComputeHash($buffer)
    } finally {
        $sha1.Dispose()
    }
    $uuidBytes = New-Object byte[] 16
    [Array]::Copy($hash, 0, $uuidBytes, 0, 16)
    $uuidBytes[6] = ($uuidBytes[6] -band 0x0F) -bor 0x50
    $uuidBytes[8] = ($uuidBytes[8] -band 0x3F) -bor 0x80

    $hex = ([System.BitConverter]::ToString($uuidBytes) -replace '-', '').ToLowerInvariant()
    return "{0}-{1}-{2}-{3}-{4}" -f `
        $hex.Substring(0, 8),
        $hex.Substring(8, 4),
        $hex.Substring(12, 4),
        $hex.Substring(16, 4),
        $hex.Substring(20, 12)
}
