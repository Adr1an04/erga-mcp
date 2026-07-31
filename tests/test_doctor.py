from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from erga_mcp.doctor import check_installation


class DoctorTests(unittest.TestCase):
    def test_low_level_init_reports_missing_core_obsidian_and_resume_setup(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text('[paths]\ndata_dir = "state"\nvault_path = ""\n')

            report = check_installation(config_path)

            self.assertFalse(report.core_ready)
            self.assertIn("config", report.checks)
            self.assertIn("tracker", report.warnings)
            self.assertIn("obsidian", report.warnings)
            self.assertIn("master_resume", report.warnings)

    def test_platform_aware_latex_resolver_marks_compiler_ready(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text('[paths]\ndata_dir = "state"\nvault_path = ""\n')

            with patch(
                "erga_mcp.doctor.resolve_latexmk_executable",
                return_value=Path("/Library/TeX/texbin/latexmk"),
            ):
                report = check_installation(config_path)

            self.assertEqual(report.checks["latexmk"], "ok")
            self.assertNotIn("latexmk", report.warnings)

    def test_missing_latex_compiler_remains_an_optional_warning(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text('[paths]\ndata_dir = "state"\nvault_path = ""\n')

            with patch(
                "erga_mcp.doctor.resolve_latexmk_executable",
                side_effect=FileNotFoundError,
            ):
                report = check_installation(config_path)

            self.assertEqual(report.warnings["latexmk"], "unavailable")
            self.assertNotIn("latexmk", report.checks)


if __name__ == "__main__":
    unittest.main()
