from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from erga_mcp.resumes.artifacts import (
    ResumeUseRecord,
    create_job_package,
    mark_resume_version_used,
    record_validated_resume_version,
)


class ResumePackageTests(unittest.TestCase):
    def test_resume_version_history_is_lossless_across_processes(self) -> None:
        with TemporaryDirectory() as directory:
            package = create_job_package(
                output_root=Path(directory) / "applications",
                cycle="fall-2026",
                application_slug="concurrent-role",
                job_url="https://jobs.example.test/concurrent",
            )
            source = package.package_dir / "source" / "resume.tex"
            pdf = package.package_dir / "artifacts" / "resume.pdf"
            source.write_text("master", encoding="utf-8")
            pdf.write_bytes(b"%PDF synthetic")
            script = (
                "import sys; from pathlib import Path; "
                "from erga_mcp.resumes.artifacts import record_validated_resume_version; "
                "record_validated_resume_version(manifest_path=Path(sys.argv[1]), "
                "application_id='app_concurrent', source_path=Path(sys.argv[2]), "
                "proposal_path=Path(sys.argv[3]), pdf_path=Path(sys.argv[4]), "
                "decision_path=Path(sys.argv[5]), validation={'returncode':0,'page_count':1})"
            )
            processes: list[subprocess.Popen[str]] = []
            for index in range(6):
                proposal = package.package_dir / "artifacts" / f"proposal-{index}.tex"
                decision = package.package_dir / "artifacts" / f"decision-{index}.json"
                proposal.write_text(f"proposal {index}", encoding="utf-8")
                decision.write_text(
                    json.dumps(
                        {
                            "master_parity": {"passed": True},
                            "catalogue": {"selected": [{"project_id": f"project-{index}"}]},
                            "selected_bullet_evidence_ids": [[f"ev-{index}"]],
                        }
                    ),
                    encoding="utf-8",
                )
                processes.append(
                    subprocess.Popen(
                        [
                            sys.executable,
                            "-c",
                            script,
                            str(package.manifest_path),
                            str(source),
                            str(proposal),
                            str(pdf),
                            str(decision),
                        ],
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                )
            outcomes = [process.communicate() for process in processes]
            self.assertTrue(all(process.returncode == 0 for process in processes), outcomes)
            manifest = json.loads(package.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["resume_versions"]), 6)

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

    def test_records_validated_generated_version_without_marking_it_used(self) -> None:
        with TemporaryDirectory() as directory:
            package = create_job_package(
                output_root=Path(directory) / "applications",
                cycle="fall-2026",
                application_slug="example-role",
                job_url="https://jobs.example.test/role",
            )
            source = package.package_dir / "source" / "resume.tex"
            proposal = package.package_dir / "artifacts" / "proposal.tex"
            pdf = package.package_dir / "artifacts" / "resume.pdf"
            decision = package.package_dir / "artifacts" / "resume-decision.json"
            source.write_text("master", encoding="utf-8")
            proposal.write_text("proposal", encoding="utf-8")
            pdf.write_bytes(b"%PDF synthetic")
            decision.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "master_parity": {"passed": True, "score": 91},
                        "catalogue": {
                            "selected": [{"project_id": "robotics"}],
                        },
                        "selected_bullet_evidence_ids": [["ev_robot"]],
                    }
                ),
                encoding="utf-8",
            )

            result = record_validated_resume_version(
                manifest_path=package.manifest_path,
                application_id="app_synthetic",
                source_path=source,
                proposal_path=proposal,
                pdf_path=pdf,
                decision_path=decision,
                validation={"returncode": 0, "page_count": 1},
            )

            self.assertFalse(result.used)
            manifest = json.loads(package.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["resume_versions"][0]["id"], result.id)
            self.assertIsNone(manifest["resume_versions"][0]["used_at"])
            self.assertIsNone(manifest["used_resume_version_id"])

            repeated = record_validated_resume_version(
                manifest_path=package.manifest_path,
                application_id="app_synthetic",
                source_path=source,
                proposal_path=proposal,
                pdf_path=pdf,
                decision_path=decision,
                validation={"returncode": 0, "page_count": 1},
            )
            self.assertEqual(repeated.id, result.id)
            self.assertEqual(
                len(manifest := json.loads(package.manifest_path.read_text())["resume_versions"]), 1
            )

            used = mark_resume_version_used(
                manifest_path=package.manifest_path,
                application_id="app_synthetic",
                version_id=result.id,
            )
            replayed = mark_resume_version_used(
                manifest_path=package.manifest_path,
                application_id="app_synthetic",
                version_id=result.id,
            )
            self.assertEqual(replayed.used_at, used.used_at)
            manifest = json.loads(package.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["used_resume_version_id"], result.id)
            self.assertEqual(len(manifest["resume_versions"]), 1)
            with self.assertRaisesRegex(ValueError, "different application"):
                mark_resume_version_used(
                    manifest_path=package.manifest_path,
                    application_id="app_other",
                    version_id=result.id,
                )

            decision.write_text(
                json.dumps(
                    {
                        "master_parity": {"passed": False},
                        "catalogue": {"selected": []},
                        "selected_bullet_evidence_ids": [],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "passing master parity"):
                record_validated_resume_version(
                    manifest_path=package.manifest_path,
                    application_id="app_synthetic",
                    source_path=source,
                    proposal_path=proposal,
                    pdf_path=pdf,
                    decision_path=decision,
                    validation={"returncode": 0, "page_count": 1},
                )

    def test_version_history_rejects_symlinked_or_malformed_manifest(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.json"
            target.write_text("{}\n", encoding="utf-8")
            symlink = root / "package.json"
            try:
                symlink.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable")
            with self.assertRaisesRegex(ValueError, "symlink"):
                mark_resume_version_used(
                    manifest_path=symlink,
                    application_id="app_synthetic",
                    version_id="resume_missing",
                )

        with TemporaryDirectory() as directory:
            manifest = Path(directory) / "package.json"
            manifest.write_text(
                json.dumps(
                    {
                        "resume_versions": [
                            {
                                "id": 42,
                                "application_id": "app_synthetic",
                                "generated_at": "not-a-date",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "stored resume version"):
                mark_resume_version_used(
                    manifest_path=manifest,
                    application_id="app_synthetic",
                    version_id="resume_missing",
                )

    def test_resume_use_record_rejects_failed_validation_and_unsupported_quality(self) -> None:
        with self.assertRaisesRegex(ValueError, "successful validation"):
            ResumeUseRecord.create(
                application_id="app_synthetic",
                source_sha256="a" * 64,
                proposal_sha256="b" * 64,
                pdf_sha256="c" * 64,
                decision_sha256="d" * 64,
                project_ids=(),
                bullet_evidence_ids=(),
                validation={"returncode": 1},
                quality={"passed": True},
            )
        with self.assertRaisesRegex(ValueError, "master-parity"):
            ResumeUseRecord.create(
                application_id="app_synthetic",
                source_sha256="a" * 64,
                proposal_sha256="b" * 64,
                pdf_sha256="c" * 64,
                decision_sha256="d" * 64,
                project_ids=(),
                bullet_evidence_ids=(),
                validation={"returncode": 0},
                quality={"passed": False},
            )


if __name__ == "__main__":
    unittest.main()
