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

            with patch("erga_mcp.resume.subprocess.run", return_value=completed):
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

    def test_tailor_rejects_new_bullets_outside_configured_character_limits(self) -> None:
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
                    "Verified outcome.",
                    "--approved",
                ]
            )

            with self.assertRaisesRegex(ValueError, "between 90 and 120"):
                main(
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

            self.assertFalse((root / "proposal").exists())


if __name__ == "__main__":
    unittest.main()
