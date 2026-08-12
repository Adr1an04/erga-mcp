from __future__ import annotations

import json
import os
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from erga_mcp.resume import create_job_package


class ResumePackageTests(unittest.TestCase):
    def test_job_package_is_owner_only_on_posix(self) -> None:
        with TemporaryDirectory() as directory:
            package = create_job_package(
                output_root=Path(directory) / "output",
                cycle="summer-2027",
                application_slug="example-role",
                job_url="https://jobs.example.test/123",
            )

            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(package.package_dir.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(package.manifest_path.stat().st_mode), 0o600)
                for name in ("source", "artifacts", "research"):
                    self.assertEqual(
                        stat.S_IMODE((package.package_dir / name).stat().st_mode), 0o700
                    )

    def test_creates_an_isolated_job_package_beneath_the_configured_root(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "applications"

            package = create_job_package(
                output_root=root,
                cycle="Fall26",
                application_slug="Fall26ExampleSystems",
                job_url="https://jobs.example.test/example-systems",
            )

            self.assertEqual(package.package_dir, root / "Fall26" / "Fall26ExampleSystems")
            self.assertTrue((package.package_dir / "source").is_dir())
            self.assertTrue((package.package_dir / "artifacts").is_dir())
            self.assertTrue((package.package_dir / "research").is_dir())
            manifest = json.loads(package.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["job_url"], "https://jobs.example.test/example-systems")
            self.assertEqual(manifest["template_status"], "not_copied")

    def test_normalizes_equivalent_term_cycles_to_one_directory_name(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "applications"
            package = create_job_package(
                output_root=root,
                cycle="2026-fall",
                application_slug="ExampleCo",
                job_url="https://jobs.example.test/role",
            )

            self.assertEqual(package.package_dir, root / "fall-2026" / "ExampleCo")
            manifest = json.loads(package.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["cycle"], "fall-2026")

    def test_refuses_a_symlinked_cycle_directory(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "applications"
            outside = Path(directory) / "outside"
            root.mkdir()
            outside.mkdir()

            real_is_symlink = Path.is_symlink

            def is_synthetic_symlink(path: Path) -> bool:
                return path == root / "Fall26" or real_is_symlink(path)

            with (
                patch.object(Path, "is_symlink", autospec=True, side_effect=is_synthetic_symlink),
                self.assertRaisesRegex(ValueError, "must not be a symlink"),
            ):
                create_job_package(
                    output_root=root,
                    cycle="Fall26",
                    application_slug="Fall26ExampleSystems",
                    job_url="https://jobs.example.test/example-systems",
                )
            self.assertFalse((outside / "Fall26ExampleSystems").exists())

    def test_refuses_unsafe_or_duplicate_package_paths(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "applications"
            with self.assertRaisesRegex(ValueError, "safe path component"):
                create_job_package(
                    output_root=root,
                    cycle="../Fall26",
                    application_slug="Fall26ExampleSystems",
                    job_url="https://jobs.example.test/example-systems",
                )
            create_job_package(
                output_root=root,
                cycle="Fall26",
                application_slug="Fall26ExampleSystems",
                job_url="https://jobs.example.test/example-systems",
            )
            with self.assertRaisesRegex(FileExistsError, "already exists"):
                create_job_package(
                    output_root=root,
                    cycle="Fall26",
                    application_slug="Fall26ExampleSystems",
                    job_url="https://jobs.example.test/example-systems",
                )


if __name__ == "__main__":
    unittest.main()
