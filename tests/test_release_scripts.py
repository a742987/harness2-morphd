from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell") or shutil.which("powershell.exe") or "powershell"

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="release supply-chain scripts are Windows PowerShell only",
)


def _run_ps(script: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *map(str, arguments),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return result


def _write_release(root: Path) -> tuple[Path, Path]:
    release = root / "release"
    electron = release / "electron"
    electron.mkdir(parents=True)
    installer = electron / "harness2-morphd Setup 0.5.0.exe"
    installer.write_bytes(b"MZ" + b"installer-fixture" * 32)
    tarball = release / "harness2-morphd-cli-0.5.0.tgz"
    tarball.write_bytes(b"npm-tarball-fixture")
    engine = root / "dist" / "morphd-engine.exe"
    engine.parent.mkdir(parents=True)
    engine.write_bytes(b"MZ" + b"engine-fixture")
    return release, engine


def _generate_sbom(release: Path, output: Path) -> None:
    result = _run_ps(
        ROOT / "scripts" / "generate_sbom.ps1",
        "-OutFile",
        str(output),
        "-ReleaseDir",
        str(release),
        "-ArtifactsOnly",
        "-Version",
        "0.5.0",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _write_signing_status(release: Path, engine: Path) -> None:
    env = {
        "TEST_SIGN_UTILS": str(ROOT / "scripts" / "sign_utils.ps1"),
        "TEST_RELEASE_DIR": str(release),
        "TEST_ENGINE": str(engine),
    }
    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            ". $env:TEST_SIGN_UTILS; Write-SigningStatus -ReleaseDir $env:TEST_RELEASE_DIR "
            "-EnginePath $env:TEST_ENGINE | Out-Null",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env={**os.environ, **env},
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _write_checksums(release: Path) -> None:
    lines = []
    for path in sorted(release.rglob("*")):
        if not path.is_file() or path.name == "SHA256SUMS-windows.txt":
            continue
        relative = path.relative_to(release).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {relative}")
    (release / "SHA256SUMS-windows.txt").write_text(
        "\n".join(lines) + "\n", encoding="ascii"
    )


def _verify(release: Path, *, allow_unsigned: bool) -> subprocess.CompletedProcess[str]:
    arguments = ["-ReleaseDir", str(release)]
    if allow_unsigned:
        arguments.append("-AllowUnsigned")
    return _run_ps(ROOT / "scripts" / "verify_release.ps1", *arguments)


def test_sbom_is_deterministic_and_hashes_release_artifacts(tmp_path):
    release, _engine = _write_release(tmp_path)
    first = tmp_path / "sbom-one.json"
    second = tmp_path / "sbom-two.json"

    _generate_sbom(release, first)
    _generate_sbom(release, second)

    assert first.read_bytes() == second.read_bytes(), "SBOM output must be byte-stable"
    payload = json.loads(first.read_text(encoding="utf-8"))
    assert payload["bomFormat"] == "CycloneDX"
    assert payload["specVersion"] == "1.5"
    assert payload["metadata"]["component"]["name"] == "harness2-morphd"

    artifacts = {
        component["name"]: component
        for component in payload["components"]
        if any(
            item.get("name") == "morphd:release-artifact" and item.get("value") == "true"
            for item in component.get("properties", [])
        )
    }
    assert "harness2-morphd-cli-0.5.0.tgz" in artifacts
    assert "electron/harness2-morphd Setup 0.5.0.exe" in artifacts
    for relative, component in artifacts.items():
        path = release / relative
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert component["hashes"] == [{"alg": "SHA-256", "content": digest}]


def test_verify_release_accepts_explicit_unsigned_build_and_rejects_tampering(tmp_path):
    release, engine = _write_release(tmp_path)
    _generate_sbom(release, release / "sbom.cdx.json")
    _write_signing_status(release, engine)
    _write_checksums(release)

    status = json.loads((release / "signing-status.json").read_text(encoding="utf-8"))
    assert status["schema"] == 1
    assert status["signed"] is False
    assert {entry["path"] for entry in status["artifacts"]} == {
        "electron/harness2-morphd Setup 0.5.0.exe",
        "platform package bin/morphd-engine.exe",
    }

    accepted = _verify(release, allow_unsigned=True)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "release verification: OK" in accepted.stdout

    rejected_unsigned = _verify(release, allow_unsigned=False)
    assert rejected_unsigned.returncode != 0
    assert "release is unsigned" in rejected_unsigned.stdout

    (release / "harness2-morphd-cli-0.5.0.tgz").write_bytes(b"tampered")
    tampered = _verify(release, allow_unsigned=True)
    assert tampered.returncode != 0
    assert "checksum mismatch" in tampered.stdout
