"""The package metadata and the server's requirements.txt must list the same dependencies."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_pyproject_and_requirements_agree():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    declared = set(project["dependencies"]) | set(project["optional-dependencies"]["dev"])
    required = {line.strip() for line in (ROOT / "requirements.txt").read_text().splitlines() if line.strip()}
    assert declared == required


def test_package_name_version_and_license():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert (project["name"], project["version"], project["license"]) == ("quoterunner", "1.0.0", "MIT")
    assert "MIT License" in (ROOT / "LICENSE").read_text()
