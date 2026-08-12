from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.config import DEFAULT_CONFIG, load_config
from erga_mcp.portfolio.skill_inventory import parse_skill_seed_csv
from erga_mcp.store import ErgaStore
from erga_mcp.tracking.onboarding import build_onboarding_card


class OnboardingViewTests(unittest.TestCase):
    def test_csv_parsing_preserves_first_display_value_and_normalizes_duplicates(self) -> None:
        self.assertEqual(
            parse_skill_seed_csv("Python, fastapi, React, Python"),
            ("Python", "fastapi", "React"),
        )
        with self.assertRaisesRegex(ValueError, "at least one"):
            parse_skill_seed_csv("  ")

    def test_completion_card_reports_truthful_state_and_stable_actions(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            repositories = root / "repositories"
            repositories.mkdir()
            master = root / "master.tex"
            master.write_text("synthetic", encoding="utf-8")
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace(
                    "portfolio_roots = []", 'portfolio_roots = ["repositories"]'
                ).replace('master_path = ""', 'master_path = "master.tex"'),
                encoding="utf-8",
            )
            config = load_config(config_path)
            store = ErgaStore(config.data_dir / "erga.sqlite3")
            store.set_skill_seeds(parse_skill_seed_csv("Python, FastAPI, React"))

            card = build_onboarding_card(config, store)
            payload = card.as_dict()

            self.assertEqual(card.title, "Erga onboarding")
            self.assertIn("master.tex", payload["fields"][0]["value"])
            self.assertIn("3 configured", str(payload))
            self.assertEqual(payload["fields"][3]["value"], str(repositories.resolve()))
            self.assertEqual(
                [action.action_id for action in card.actions],
                [
                    "onboarding.skills.help",
                    "onboarding.roots.help",
                    "git.scan",
                    "settings.show",
                ],
            )

    def test_empty_skill_inventory_is_not_fabricated(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            config = load_config(config_path)
            store = ErgaStore(config.data_dir / "erga.sqlite3")

            card = build_onboarding_card(config, store)

            self.assertIn("Not configured", card.as_text())
            self.assertNotIn("Python", card.as_text())


if __name__ == "__main__":
    unittest.main()
