# Shared Authenticode helpers. Windows PowerShell 5.1 compatible.
#
# Signing is opt-in: set MORPHD_SIGN_CERT to a .pfx/.p12 file and
# MORPHD_SIGN_PASSWORD to its password. Without a certificate the build stays
# unsigned and emits an explicit warning instead of silently pretending to sign.

function Resolve-SignTool {
    $roots = @(
        (Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"),
        (Join-Path $env:ProgramFiles "Windows Kits\10\bin")
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }

    foreach ($root in $roots) {
        $found = Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName "x64\signtool.exe" } |
            Where-Object { Test-Path -LiteralPath $_ } |
            Select-Object -First 1
        if ($found) { return $found }
    }

    $command = Get-Command "signtool.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) { return $command.Source }
    return $null
}

function Get-SigningCertificate {
    param([string]$CertificatePath = "")
    if (-not $CertificatePath) { $CertificatePath = $env:MORPHD_SIGN_CERT }
    return $CertificatePath
}

function Test-AuthenticodeSigned {
    param([Parameter(Mandatory = $true)][string]$Path)

    $signtool = Resolve-SignTool
    if (-not $signtool -or -not (Test-Path -LiteralPath $Path)) { return $false }
    & $signtool verify /pa /q $Path *> $null
    return $LASTEXITCODE -eq 0
}

function Write-SigningStatus {
    param(
        [Parameter(Mandatory = $true)][string]$ReleaseDir,
        [string]$EnginePath = "",
        [string]$EngineLabel = "platform package bin/morphd-engine.exe"
    )

    $records = @()
    $electronDir = Join-Path $ReleaseDir "electron"
    if (Test-Path -LiteralPath $electronDir) {
        foreach ($installer in @(Get-ChildItem -LiteralPath $electronDir -File -Filter "*.exe" | Sort-Object Name)) {
            $records += [ordered]@{
                path = "electron/$($installer.Name)"
                signed = [bool](Test-AuthenticodeSigned -Path $installer.FullName)
            }
        }
    }
    if ($EnginePath) {
        $records += [ordered]@{
            path = $EngineLabel
            signed = [bool](Test-AuthenticodeSigned -Path $EnginePath)
        }
    }

    $certificateConfigured = [bool](Get-SigningCertificate)
    $unsigned = @($records | Where-Object { -not $_.signed })
    $status = [ordered]@{
        schema = 1
        signed = [bool]($certificateConfigured -and ($unsigned.Count -eq 0))
        certificate_configured = $certificateConfigured
        timestamp_url = "http://timestamp.digicert.com"
        artifacts = $records
        note = $(if ($certificateConfigured) {
            "Authenticode signatures were requested via MORPHD_SIGN_CERT."
        } else {
            "Unsigned build: MORPHD_SIGN_CERT is not set. SmartScreen may warn about an unknown publisher."
        })
    }
    [System.IO.File]::WriteAllText(
        (Join-Path $ReleaseDir "signing-status.json"),
        (($status | ConvertTo-Json -Depth 5) + "`n"),
        (New-Object System.Text.UTF8Encoding($false))
    )
    if (-not $status.signed) {
        Write-Warning "This release is unsigned; see signing-status.json."
    }
    return $status
}

function Invoke-AuthenticodeSign {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$CertificatePath = "",
        [string]$CertificatePassword = "",
        [string]$Description = "harness2-morphd",
        [string]$TimestampUrl = "http://timestamp.digicert.com"
    )

    $certificate = Get-SigningCertificate -CertificatePath $CertificatePath
    if (-not $certificate) {
        Write-Warning "Authenticode signing skipped (MORPHD_SIGN_CERT is not set): $Path"
        return $false
    }
    if (-not (Test-Path -LiteralPath $certificate)) {
        throw "Signing certificate was not found at $certificate"
    }
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Artifact to sign was not found at $Path"
    }

    $signtool = Resolve-SignTool
    if (-not $signtool) {
        throw "signtool.exe was not found. Install the Windows 10/11 SDK or add signtool to PATH."
    }
    if (-not $CertificatePassword) { $CertificatePassword = $env:MORPHD_SIGN_PASSWORD }

    $arguments = @(
        "sign",
        "/fd", "SHA256",
        "/f", $certificate,
        "/tr", $TimestampUrl,
        "/td", "SHA256",
        "/d", $Description
    )
    if ($CertificatePassword) { $arguments += @("/p", $CertificatePassword) }
    $arguments += $Path

    & $signtool @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "signtool sign failed for $Path with exit code $LASTEXITCODE"
    }
    & $signtool verify /pa /q $Path
    if ($LASTEXITCODE -ne 0) {
        throw "signtool verify failed for $Path with exit code $LASTEXITCODE"
    }
    Write-Host "signed: $Path"
    return $true
}
