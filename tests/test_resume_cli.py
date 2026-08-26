from __future__ import annotations

import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from erga_mcp.cli import main
from erga_mcp.resumes.artifacts import LatexValidation


class ResumeCliTests(unittest.TestCase):
    def _json_command(self, arguments: list[str]) -> dict[str, object]:
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(arguments), 0)
        return json.loads(output.getvalue())

    def test_creates_a_local_resume_proposal_from_approved_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            main(["init", "--config", str(config)])
            evidence = self._json_command(
                [
                    "evidence",
                    "add",
                    "--config",
                    str(config),
                    "--source-ref",
                    "Career.md#Project",
                    "--text",
                    "Verified outcome.",
                    "--approved",
                ]
            )
            resume = root / "resume.tex"
            resume.write_text("\\begin{document}\n\\end{document}\n", encoding="utf-8")

            proposal = self._json_command(
                [
                    "resume",
                    "propose",
                    "--config",
                    str(config),
                    "--resume",
                    str(resume),
                    "--output-dir",
                    str(root / "proposals"),
                    "--latex-snippet",
                    "\\item Verified outcome.",
                    "--evidence-id",
                    str(evidence["id"]),
                ]
            )

            self.assertTrue(Path(str(proposal["diff_path"])).exists())
            self.assertEqual(
                resume.read_text(encoding="utf-8"), "\\begin{document}\n\\end{document}\n"
            )

    def test_validates_an_explicit_local_proposal_with_latexmk(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            main(["init", "--config", str(config)])
            proposal = root / "proposal.tex"
            proposal.write_text("\\begin{document}ok\\end{document}\n", encoding="utf-8")
            completed = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="compiled", stderr=""
            )

            with patch("erga_mcp.resumes.artifacts.subprocess.run", return_value=completed):
                result = self._json_command(
                    [
                        "resume",
                        "validate",
                        "--config",
                        str(config),
                        "--proposal",
                        str(proposal),
                        "--latexmk",
                        sys.executable,
                    ]
                )

            self.assertEqual(result["returncode"], 0)
            self.assertEqual(result["stdout"], "compiled")

    def test_tailor_job_runs_the_deterministic_end_to_end_cli_path(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            template = root / "resume.tex"
            template.write_text(
                "\\section{Experience}\n"
                "\\resumeSubheading{Engineer}{2026}{Synthetic}{Remote}\n"
                "\\resumeItemListStart\n"
                "\\resumeItem{Designed marketing campaign assets.}\n"
                "\\resumeItem{Owned Kubernetes services and production support.}\n"
                "\\resumeItemListEnd\n",
                encoding="utf-8",
            )
            main(["init", "--config", str(config)])
            self._json_command(
                [
                    "resume",
                    "settings",
                    "set",
                    "--config",
                    str(config),
                    "--template-path",
                    str(template),
                    "--editable-section",
                    "Experience",
                ]
            )
            self._json_command(
                [
                    "evidence",
                    "add",
                    "--config",
                    str(config),
                    "--source-ref",
                    "synthetic:platform",
                    "--text",
                    "Owned Kubernetes services and production support.",
                    "--approved",
                ]
            )

            with patch(
                "erga_mcp.cli.fetch_job_snapshot",
                return_value=(
                    "Infrastructure Engineer. Required: Kubernetes production ownership and "
                    "incident response."
                ),
            ):
                result = self._json_command(
                    [
                        "resume",
                        "tailor-job",
                        "--config",
                        str(config),
                        "--job-url",
                        "https://jobs.example.test/infrastructure",
                        "--output-dir",
                        str(root / "proposal"),
                    ]
                )

            self.assertTrue(result["meaningful_change"])
            self.assertIsNone(result["validation"])
            self.assertTrue(result["selected_evidence_ids"])
            proposed = Path(str(result["proposed_tex_path"])).read_text(encoding="utf-8")
            self.assertLess(
                proposed.index("Owned Kubernetes"), proposed.index("Designed marketing")
            )

    def test_one_command_tailor_owns_output_and_checks_the_pdf(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            template = root / "resume.tex"
            template.write_text(
                "\\section{Experience}\n"
                "\\resumeSubheading{Engineer}{2026}{Synthetic}{Remote}\n"
                "\\resumeItemListStart\n"
                "\\resumeItem{Owned Kubernetes services and production support.}\n"
                "\\resumeItemListEnd\n",
                encoding="utf-8",
            )
            main(["init", "--config", str(config)])
            self._json_command(
                [
                    "resume",
                    "settings",
                    "set",
                    "--config",
                    str(config),
                    "--template-path",
                    str(template),
                    "--editable-section",
                    "Experience",
                ]
            )
            self._json_command(
                [
                    "evidence",
                    "add",
                    "--config",
                    str(config),
                    "--source-ref",
                    "synthetic:platform",
                    "--text",
                    "Owned Kubernetes services and production support.",
                    "--approved",
                ]
            )
            output = StringIO()
            with (
                patch(
                    "erga_mcp.cli.fetch_job_snapshot",
                    return_value="Infrastructure Engineer. Required: Kubernetes ownership.",
                ),
                patch(
                    "erga_mcp.cli.validate_latex_proposal",
                    return_value=LatexValidation((), 0, "checked", ""),
                ) as validation,
                redirect_stdout(output),
            ):
                exit_code = main(
                    [
                        "tailor",
                        "https://jobs.example.test/infrastructure",
                        "--config",
                        str(config),
                    ]
                )

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Résumé draft needs one more review", rendered)
            self.assertIn("did not produce a PDF", rendered)
            self.assertIn("Nothing was sent or submitted", rendered)
            self.assertIn(str(root / "output" / "tailored"), rendered)
            validation.assert_called_once()

    def test_tailor_records_bullets_below_configured_minimum_without_failing(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            template = root / "resume.tex"
            template.write_text("\\section{Experience}\nexisting\n", encoding="utf-8")
            main(["init", "--config", str(config)])
            self._json_command(
                [
                    "resume",
                    "settings",
                    "set",
                    "--config",
                    str(config),
                    "--template-path",
                    str(template),
                    "--editable-section",
                    "Experience",
                    "--bullet-min-chars",
                    "90",
                    "--bullet-target-chars",
                    "105",
                    "--bullet-max-chars",
                    "120",
                ]
            )
            evidence = self._json_command(
                [
                    "evidence",
                    "add",
                    "--config",
                    str(config),
                    "--source-ref",
                    "approved",
                    "--text",
                    "Too short",
                    "--approved",
                ]
            )

            proposal = self._json_command(
                [
                    "resume",
                    "tailor",
                    "--config",
                    str(config),
                    "--section",
                    "Experience",
                    "--latex-content",
                    "\\resumeItem{Too short}",
                    "--output-dir",
                    str(root / "proposal"),
                    "--evidence-id",
                    str(evidence["id"]),
                ]
            )

            report = json.loads(
                Path(str(proposal["claim_report_path"])).read_text(encoding="utf-8")
            )
            lengths = report["constraints"]["bullet_characters"]
            self.assertTrue(lengths["passed"])
            self.assertEqual(lengths["violations"], [])
            self.assertEqual(lengths["soft_deviations"][0]["length"], 9)
            self.assertTrue((root / "proposal" / "proposal.tex").exists())

    def test_friendly_tailor_accepts_a_job_file_without_fetching_the_web(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            template = root / "resume.tex"
            template.write_text(
                "\\section{Experience}\n"
                "\\resumeSubheading{Engineer}{2026}{Synthetic}{Remote}\n"
                "\\resumeItemListStart\n"
                "\\resumeItem{Built Python services with PostgreSQL for internal users.}\n"
                "\\resumeItemListEnd\n",
                encoding="utf-8",
            )
            job = root / "job.txt"
            job.write_text(
                "Platform Engineering Intern. Build Python services backed by PostgreSQL and "
                "support reliable production systems.",
                encoding="utf-8",
            )
            main(["init", "--config", str(config)])
            self._json_command(
                [
                    "resume",
                    "settings",
                    "set",
                    "--config",
                    str(config),
                    "--template-path",
                    str(template),
                    "--editable-section",
                    "Experience",
                ]
            )
            self._json_command(
                [
                    "evidence",
                    "add",
                    "--config",
                    str(config),
                    "--source-ref",
                    "synthetic:platform",
                    "--text",
                    "Built Python services with PostgreSQL for internal users.",
                    "--approved",
                ]
            )

            with patch("erga_mcp.cli.fetch_job_snapshot") as fetch:
                result = self._json_command(
                    [
                        "tailor",
                        "--job-file",
                        str(job),
                        "--company",
                        "Acme",
                        "--role",
                        "Platform Intern",
                        "--preset",
                        "concise",
                        "--no-validate",
                        "--json",
                        "--config",
                        str(config),
                    ]
                )

            fetch.assert_not_called()
            self.assertEqual(result["company"], "Acme")
            self.assertEqual(result["role"], "Platform Intern")
            self.assertEqual(result["preset"], "concise")
            self.assertEqual(result["resolved_limits"]["max_pages"], 1)


if __name__ == "__main__":
    unittest.main()
