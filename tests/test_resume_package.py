from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from recruiting_pipeline.resume import create_job_package


class ResumePackageTests(unittest.TestCase):
    def test_creates_an_isolated_job_package_beneath_the_configured_root(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "applications"

            package = create_job_package(
                output_root=root,
                cycle="Fall26",
                application_slug="Fall26Palantir",
                job_url="https://jobs.example.test/palantir",
            )

            self.assertEqual(package.package_dir, root / "Fall26" / "Fall26Palantir")
            self.assertTrue((package.package_dir / "source").is_dir())
            self.assertTrue((package.package_dir / "artifacts").is_dir())
            self.assertTrue((package.package_dir / "research").is_dir())
            manifest = json.loads(package.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["job_url"], "https://jobs.example.test/palantir")
            self.assertEqual(manifest["template_status"], "not_copied")

    def test_refuses_a_symlinked_cycle_directory(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "applications"
            outside = Path(directory) / "outside"
            root.mkdir()
            outside.mkdir()
            (root / "Fall26").symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(ValueError, "must not be a symlink"):
                create_job_package(
                    output_root=root,
                    cycle="Fall26",
                    application_slug="Fall26Palantir",
                    job_url="https://jobs.example.test/palantir",
                )
            self.assertFalse((outside / "Fall26Palantir").exists())

    def test_refuses_unsafe_or_duplicate_package_paths(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "applications"
            with self.assertRaisesRegex(ValueError, "safe path component"):
                create_job_package(
                    output_root=root,
                    cycle="../Fall26",
                    application_slug="Fall26Palantir",
                    job_url="https://jobs.example.test/palantir",
                )
            create_job_package(
                output_root=root,
                cycle="Fall26",
                application_slug="Fall26Palantir",
                job_url="https://jobs.example.test/palantir",
            )
            with self.assertRaisesRegex(FileExistsError, "already exists"):
                create_job_package(
                    output_root=root,
                    cycle="Fall26",
                    application_slug="Fall26Palantir",
                    job_url="https://jobs.example.test/palantir",
                )


if __name__ == "__main__":
    unittest.main()
