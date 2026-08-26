from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.config import DEFAULT_CONFIG, load_config
from erga_mcp.portfolio.roots import detected_portfolio_root


class ConfigTests(unittest.TestCase):
    def test_detects_only_conventional_direct_project_roots_without_home_crawl(self) -> None:
        with TemporaryDirectory() as directory:
            home = Path(directory)
            projects = home / "Projects"
            repository = projects / "example"
            repository.mkdir(parents=True)
            (repository / ".git").mkdir()
            unrelated = home / "random" / "nested" / "repo" / ".git"
            unrelated.mkdir(parents=True)

            self.assertEqual(detected_portfolio_root(home), projects.resolve())

    def test_loads_canonical_deduplicated_explicit_portfolio_roots(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            repositories = root / "repositories"
            repositories.mkdir()
            config_path = root / "config.toml"
            config_path.write_text(
                '[paths]\nportfolio_roots = ["repositories", "repositories"]\n',
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertEqual(config.portfolio_roots, (repositories.resolve(),))

    def test_rejects_missing_or_symlinked_portfolio_roots(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            config_path.write_text(
                '[paths]\nportfolio_roots = ["missing"]\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "portfolio_roots"):
                load_config(config_path)

            target = root / "target"
            target.mkdir()
            link = root / "link"
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError:
                self.skipTest("directory symlinks are unavailable")
            config_path.write_text(
                '[paths]\nportfolio_roots = ["link"]\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "symlink"):
                load_config(config_path)

    def test_default_resume_shape_is_one_page_with_balanced_entry_depth(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")

            resume = load_config(config_path).resume

            self.assertEqual(resume.max_pages, 1)
            self.assertEqual(resume.experience_min_bullets, 2)
            self.assertEqual(resume.experience_max_bullets, 4)
            self.assertEqual(resume.project_min_bullets, 2)
            self.assertEqual(resume.project_max_bullets, 4)

    def test_resume_entry_bullet_limits_must_be_ordered_and_positive(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace("experience_min_bullets = 2", "experience_min_bullets = 0"),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "experience bullet limits"):
                load_config(config_path)

            config_path.write_text(
                DEFAULT_CONFIG.replace("project_max_bullets = 4", "project_max_bullets = 1"),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "project bullet limits"):
                load_config(config_path)

    def test_keryx_is_disabled_by_default_and_requires_explicit_opt_in(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")

            self.assertFalse(load_config(config_path).keryx.enabled)

    def test_orbit_images_are_temporary_by_default_and_require_a_boolean_preset(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            self.assertFalse(load_config(config_path).orbit.retain_generated_images)

            config_path.write_text(
                '[orbit]\nretain_generated_images = "yes"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "retain_generated_images"):
                load_config(config_path)

    def test_loads_client_neutral_career_tool_profile(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text('[mcp]\ntool_profile = "career"\n', encoding="utf-8")

            config = load_config(config_path)

            self.assertEqual(config.mcp.tool_profile, "career")

    def test_legacy_config_without_mcp_table_keeps_compatible_default_profile(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text('[paths]\ndata_dir = "state"\n', encoding="utf-8")

            config = load_config(config_path)

            self.assertEqual(config.mcp.tool_profile, "default")

    def test_loads_explicit_private_career_tool_profile(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text('[mcp]\ntool_profile = "career-private"\n', encoding="utf-8")

            config = load_config(config_path)

            self.assertEqual(config.mcp.tool_profile, "career-private")

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
            self.assertEqual(config.resume.minimum_page_fill_ratio, 0.82)
            self.assertTrue(config.resume.require_unique_lead_verbs)
            self.assertEqual(config.resume.output_root, config_path.parent / "applications")
        self.assertEqual(config.resume.output_pdf_name, "Firstname_Lastname_Resume.pdf")

    def test_loads_project_inventory_settings(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(
                """
[resume]
project_inventory_path = "projects.json"
project_count = 3
""".strip(),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertEqual(
                config.resume.project_inventory_path, config_path.parent / "projects.json"
            )
            self.assertEqual(config.resume.project_count, 3)

    def test_lead_verb_uniqueness_can_be_disabled_as_a_style_preference(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(
                "[resume]\nrequire_unique_lead_verbs = false\n",
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertFalse(config.resume.require_unique_lead_verbs)

    def test_rejects_invalid_minimum_page_fill_ratio(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(
                "[resume]\nminimum_page_fill_ratio = 1.2\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "minimum_page_fill_ratio"):
                load_config(config_path)

    def test_project_inventory_required_mode_needs_a_catalogue_path(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(
                '[resume]\nproject_selection_mode = "inventory_required"\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "project_inventory_path"):
                load_config(config_path)

    def test_loads_explicit_project_inventory_selection_mode(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(
                """
[resume]
project_inventory_path = "projects.json"
project_selection_mode = "inventory_required"
""".strip(),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertEqual(config.resume.project_selection_mode, "inventory_required")

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

    def test_rejects_resume_output_names_with_cross_platform_path_components(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            for output_pdf_name in (r"..\resume.pdf", r"C:\temp\resume.pdf"):
                with self.subTest(output_pdf_name=output_pdf_name):
                    config_path.write_text(
                        f"[resume]\noutput_pdf_name = {output_pdf_name!r}\n",
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(ValueError, "output_pdf_name"):
                        load_config(config_path)

    def test_loads_active_tracker_cycles_for_mail_reconciliation(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace("enabled = false", "enabled = true")
                .replace('tracker_dir = ""', 'tracker_dir = "tracker"')
                .replace(
                    "active_cycles = []",
                    'active_cycles = ["Fall 2026", "Winter 2027", "Spring 2027", "Summer 2027"]',
                ),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertEqual(
                config.tracker.active_cycles,
                ("Fall 2026", "Winter 2027", "Spring 2027", "Summer 2027"),
            )

    def test_rejects_unknown_tracker_seasons(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace("active_cycles = []", 'active_cycles = ["Autumn 2027"]'),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Winter, Spring, Summer, or Fall"):
                load_config(config_path)


if __name__ == "__main__":
    unittest.main()
