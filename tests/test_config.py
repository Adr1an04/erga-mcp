from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.config import DEFAULT_CONFIG, load_config


class ConfigTests(unittest.TestCase):
    def test_load_config_resolves_relative_paths_from_config_directory(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(
                """
[paths]
data_dir = "state"
vault_path = "vault"

[mail]
folder = "Job Applications"
""".strip()
            )

            config = load_config(config_path)

            self.assertEqual(config.data_dir, config_path.parent / "state")
            self.assertEqual(config.vault_path, config_path.parent / "vault")
            self.assertEqual(config.mail_folder, "Job Applications")
            self.assertEqual(config.mail_client_id, "")
            self.assertEqual(config.mail_accounts_url, "https://accounts.zoho.com")

    def test_loads_non_secret_scheduled_zoho_settings(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(
                """
[mail]
provider = "zoho"
client_id = "synthetic-client-id"
accounts_url = "https://accounts.zoho.eu"
""".strip()
            )

            config = load_config(config_path)

            self.assertEqual(config.mail_client_id, "synthetic-client-id")
            self.assertEqual(config.mail_accounts_url, "https://accounts.zoho.eu")

    def test_loads_a_template_agnostic_resume_profile(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(
                """
[resume]
template_path = "templates/master.tex"
editable_sections = ["experience", "projects"]
bullet_min_chars = 90
bullet_target_chars = 105
bullet_max_chars = 120
max_pages = 1
output_root = "applications"
latexmk = "latexmk"
""".strip()
            )

            config = load_config(config_path)

            self.assertEqual(
                config.resume.template_path, config_path.parent / "templates/master.tex"
            )
            self.assertEqual(config.resume.editable_sections, ("experience", "projects"))
            self.assertEqual(config.resume.bullet_min_chars, 90)
            self.assertEqual(config.resume.bullet_target_chars, 105)
            self.assertEqual(config.resume.bullet_max_chars, 120)
            self.assertEqual(config.resume.output_root, config_path.parent / "applications")
        self.assertEqual(config.resume.output_pdf_name, "Firstname_Lastname_Resume.pdf")

    def test_rejects_an_invalid_resume_bullet_range(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(
                """
[resume]
bullet_min_chars = 120
bullet_target_chars = 105
bullet_max_chars = 90
""".strip()
            )

            with self.assertRaisesRegex(ValueError, "bullet character lengths"):
                load_config(config_path)

    def test_loads_active_tracker_cycles_for_mail_reconciliation(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace("enabled = false", "enabled = true")
                .replace('tracker_dir = ""', 'tracker_dir = "tracker"')
                .replace("active_cycles = []", 'active_cycles = ["Fall 2026", "Spring 2027"]'),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertEqual(config.tracker.active_cycles, ("Fall 2026", "Spring 2027"))


if __name__ == "__main__":
    unittest.main()
