#!/usr/bin/env python3
"""Best-effort Python metadata probe for scripts/generate_sbom.ps1.

Prints one JSON object to stdout. This helper never fails the caller: any
problem is reported as {"ok": false, "error": "..."} and the PowerShell script
falls back to parsing pyproject.toml.

The probe deliberately does not emit absolute file paths so an SBOM stays
portable and stable across build hosts.
"""

from __future__ import annotations

import importlib.metadata as importlib_metadata
import json
import platform
import re
import sys
from pathlib import Path

REQUIREMENT_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_name(requirement: str) -> str | None:
    match = REQUIREMENT_RE.match(requirement)
    return match.group(1) if match else None


def parse_pyproject(path: Path) -> dict:
    try:
        import tomllib
    except ImportError:  # Python < 3.11; the product requires >= 3.12.
        return {"error": "tomllib is unavailable"}

    with path.open("rb") as handle:
        data = tomllib.load(handle)

    project = data.get("project") or {}
    build_system = data.get("build-system") or {}
    optional = project.get("optional-dependencies") or {}

    return {
        "name": project.get("name"),
        "version": project.get("version"),
        "license": project.get("license"),
        "requires_python": project.get("requires-python"),
        "dependencies": list(project.get("dependencies") or []),
        "optional_dependencies": {
            str(group): list(requirements or [])
            for group, requirements in sorted(optional.items())
        },
        "build_requires": list(build_system.get("requires") or []),
    }


def installed_distributions(project_name: str | None) -> list[dict]:
    project_normalized = normalize_name(project_name) if project_name else None
    seen: set[tuple[str, str]] = set()
    result: list[dict] = []

    for distribution in importlib_metadata.distributions():
        metadata = distribution.metadata
        name = metadata.get("Name")
        if not name:
            continue
        normalized = normalize_name(name)
        if project_normalized and normalized == project_normalized:
            continue
        version = distribution.version or ""
        key = (normalized, version)
        if key in seen:
            continue
        seen.add(key)

        license_value = metadata.get("License-Expression") or metadata.get("License")
        if license_value:
            license_value = " ".join(str(license_value).split())
            if len(license_value) > 120:
                license_value = None

        result.append(
            {
                "name": name,
                "version": version,
                "license": license_value,
            }
        )

    return sorted(result, key=lambda item: (normalize_name(item["name"]), item["version"]))


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] != "--pyproject":
        print(json.dumps({"ok": False, "error": "usage: sbom_python_probe.py --pyproject <path>"}))
        return 0

    pyproject = Path(sys.argv[2])
    if not pyproject.is_file():
        print(json.dumps({"ok": False, "error": "pyproject.toml was not found"}))
        return 0

    try:
        project = parse_pyproject(pyproject)
        if "error" in project:
            print(json.dumps({"ok": False, "error": project["error"]}))
            return 0

        installed = installed_distributions(project.get("name"))
        declared = []
        for requirement in project["dependencies"]:
            name = requirement_name(requirement)
            if name:
                declared.append(
                    {
                        "name": name,
                        "requirement": requirement,
                        "group": "project",
                        "scope": "required",
                    }
                )
        for group, requirements in project["optional_dependencies"].items():
            for requirement in requirements:
                name = requirement_name(requirement)
                if name:
                    declared.append(
                        {
                            "name": name,
                            "requirement": requirement,
                            "group": group,
                            "scope": "optional",
                        }
                    )
        for requirement in project["build_requires"]:
            name = requirement_name(requirement)
            if name:
                declared.append(
                    {
                        "name": name,
                        "requirement": requirement,
                        "group": "build-system",
                        "scope": "optional",
                    }
                )

        # Resolve declared names against the installed environment.
        installed_by_name: dict[str, str] = {}
        for package in installed:
            installed_by_name.setdefault(normalize_name(package["name"]), package["version"])
        for item in declared:
            item["installedVersion"] = installed_by_name.get(normalize_name(item["name"]))

        payload = {
            "ok": True,
            "python": {
                "implementation": platform.python_implementation(),
                "version": platform.python_version(),
                "requiresPython": project.get("requires_python"),
            },
            "project": {
                "name": project.get("name"),
                "version": project.get("version"),
                "license": project.get("license"),
            },
            "dependencies": sorted(
                declared, key=lambda item: (item["group"], normalize_name(item["name"]))
            ),
            "installed": installed,
        }
        print(json.dumps(payload, sort_keys=True, ensure_ascii=True))
    except Exception as exc:  # noqa: BLE001 - the caller intentionally degrades gracefully.
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
