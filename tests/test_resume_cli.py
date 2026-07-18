from __future__ import annotations

import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from recruiting_pipeline.cli import main


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
            latexmk = root / "fake-latexmk"
            latexmk.write_text("#!/bin/sh\nprintf compiled\n", encoding="utf-8")
            latexmk.chmod(0o755)

            result = self._json_command(
                [
                    "resume",
                    "validate",
                    "--config",
                    str(config),
                    "--proposal",
                    str(proposal),
                    "--latexmk",
                    str(latexmk),
                ]
            )

            self.assertEqual(result["returncode"], 0)
            self.assertEqual(result["stdout"], "compiled")


if __name__ == "__main__":
    unittest.main()
