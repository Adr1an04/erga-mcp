from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from erga_mcp.resume import resolve_latexmk_executable, validate_latex_proposal


class ResumeValidationTests(unittest.TestCase):
    def test_compiles_only_a_proposed_tex_file_with_a_user_selected_latexmk(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            proposal = root / "proposal.tex"
            proposal.write_text("\\begin{document}ok\\end{document}\n", encoding="utf-8")
            completed = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="validated proposal.tex\n", stderr=""
            )

            with patch("erga_mcp.resume.subprocess.run", return_value=completed) as run:
                result = validate_latex_proposal(proposal, latexmk=Path(sys.executable))

            self.assertEqual(result.returncode, 0)
            self.assertIn("proposal.tex", result.stdout)
            self.assertEqual(run.call_args.args[0][-1], "proposal.tex")
            self.assertIn("-no-shell-escape", run.call_args.args[0])
            self.assertEqual(
                proposal.read_text(encoding="utf-8"), "\\begin{document}ok\\end{document}\n"
            )

    def test_resolves_mactex_when_launchd_path_omits_texbin(self) -> None:
        with TemporaryDirectory() as directory:
            texbin = Path(directory)
            latexmk = texbin / "latexmk"
            latexmk.write_text("synthetic compiler", encoding="utf-8")

            with (
                patch("erga_mcp.resume.sys.platform", "darwin"),
                patch("erga_mcp.resume.shutil.which", return_value=None),
                patch("erga_mcp.resume._MACOS_TEXBIN", texbin),
                patch("erga_mcp.resume.os.access", return_value=True),
            ):
                resolved = resolve_latexmk_executable(Path("latexmk"))

            self.assertEqual(resolved, latexmk)

    def test_adds_compiler_directory_to_child_path(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            proposal = root / "proposal.tex"
            proposal.write_text("\\begin{document}ok\\end{document}\n", encoding="utf-8")
            compiler_dir = root / "compiler"
            compiler_dir.mkdir()
            latexmk = compiler_dir / "latexmk"
            latexmk.write_text("synthetic compiler", encoding="utf-8")
            completed = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="child engine found\n", stderr=""
            )

            with (
                patch.dict("os.environ", {"PATH": os.pathsep.join(("one", "two"))}, clear=False),
                patch("erga_mcp.resume.shutil.which", return_value=str(latexmk)),
                patch("erga_mcp.resume.subprocess.run", return_value=completed) as run,
            ):
                result = validate_latex_proposal(proposal, latexmk=latexmk)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "child engine found\n")
            self.assertEqual(
                run.call_args.kwargs["env"]["PATH"].split(os.pathsep)[0], str(compiler_dir)
            )


if __name__ == "__main__":
    unittest.main()
