from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from recruiting_pipeline.resume import validate_latex_proposal


class ResumeValidationTests(unittest.TestCase):
    def test_compiles_only_a_proposed_tex_file_with_a_user_selected_latexmk(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            proposal = root / "proposal.tex"
            proposal.write_text("\\begin{document}ok\\end{document}\n", encoding="utf-8")
            latexmk = root / "fake-latexmk"
            latexmk.write_text("#!/bin/sh\nprintf 'validated %s\\n' \"$*\"\n", encoding="utf-8")
            latexmk.chmod(0o755)

            result = validate_latex_proposal(proposal, latexmk=latexmk)

            self.assertEqual(result.returncode, 0)
            self.assertIn("proposal.tex", result.stdout)
            self.assertEqual(
                proposal.read_text(encoding="utf-8"), "\\begin{document}ok\\end{document}\n"
            )


if __name__ == "__main__":
    unittest.main()
