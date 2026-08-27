"""Regression tests for setuptools package discovery."""

from __future__ import annotations

import unittest
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # type: ignore

REPO = Path(__file__).resolve().parents[1]


def _python_packages_under(root: Path, prefix: str) -> list[str]:
    """List importable package names rooted at prefix (requires __init__.py)."""
    base = root / prefix
    found: list[str] = []
    if not (base / "__init__.py").is_file():
        return found
    found.append(prefix)
    for init in base.rglob("__init__.py"):
        rel = init.parent.relative_to(root)
        name = ".".join(rel.parts)
        if name not in found:
            found.append(name)
    return found


class PackagingDiscoveryTests(unittest.TestCase):
    """pyproject.toml package discovery, licensing, and console entry points."""

    def test_pyproject_limits_discovery_to_audio_path_checker(self):
        data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
        find_cfg = data["tool"]["setuptools"]["packages"]["find"]
        self.assertEqual(find_cfg["where"], ["."])
        self.assertEqual(find_cfg["include"], ["audio_path_checker*"])
        self.assertIn("artifacts*", find_cfg["exclude"])
        self.assertIn("tests*", find_cfg["exclude"])

    def test_repo_packages_exclude_artifacts_dir(self):
        packages = _python_packages_under(REPO, "audio_path_checker")
        self.assertIn("audio_path_checker", packages)
        self.assertTrue(any(p.startswith("audio_path_checker.") for p in packages))
        artifacts_root = REPO / "artifacts"
        if artifacts_root.is_dir():
            self.assertFalse((artifacts_root / "__init__.py").exists())
        data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
        include = data["tool"]["setuptools"]["packages"]["find"]["include"]
        self.assertTrue(all(p.startswith("audio_path_checker") for p in include))

    def test_license_uses_spdx_not_classifier(self):
        data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(data["project"]["license"], "MIT")
        self.assertEqual(data["project"].get("license-files"), ["LICENSE"])
        classifiers = data["project"].get("classifiers", [])
        self.assertFalse(any(c.startswith("License ::") for c in classifiers))

    def test_entry_points_preserved(self):
        data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(
            data["project"]["scripts"]["windows-audio-checker-cli"],
            "audio_path_checker.__main__:main",
        )
        self.assertEqual(
            data["project"]["gui-scripts"]["windows-audio-checker"],
            "audio_path_checker.gui:main",
        )


if __name__ == "__main__":
    unittest.main()
