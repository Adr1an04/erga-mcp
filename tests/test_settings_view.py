from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from erga_mcp.config import DEFAULT_CONFIG, load_config
from erga_mcp.store import ErgaStore
from erga_mcp.tracking.settings import build_settings_card


class SettingsViewTests(unittest.TestCase):
    def test_settings_card_reports_connection_state_without_credentials(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace('client_id = ""', 'client_id = "super-secret-client"'),
                encoding="utf-8",
            )
            (root / "discord-bridge.json").write_text(
                json.dumps(
                    {
                        "token": "never-render-this",
                        "backend_command": "/private/credential/path",
                    }
                ),
                encoding="utf-8",
            )
            config = load_config(config_path)
            store = ErgaStore(config.data_dir / "erga.sqlite3")
            store.set_skill_seeds(["Python"])

            card = build_settings_card(config, store)
            rendered = json.dumps(card.as_dict())

            self.assertIn("Configured", rendered)
            self.assertIn("1 configured", rendered)
            self.assertNotIn("super-secret-client", rendered)
            self.assertNotIn("never-render-this", rendered)
            self.assertNotIn("credential/path", rendered)
            self.assertNotIn(str(config.data_dir), rendered)

    def test_missing_mobile_settings_explain_next_steps_and_offer_one_tap_imports(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            config = load_config(config_path)
            store = ErgaStore(config.data_dir / "erga.sqlite3")
            store.add_evidence(
                source_ref="fixture:approved",
                text="Built APIs with Python and FastAPI.",
                approved=True,
            )

            with patch(
                "erga_mcp.tracking.settings.detected_portfolio_root",
                return_value=Path("/private/detected/projects"),
            ):
                card = build_settings_card(config, store, host_integration="hermes")

            rendered = card.as_text()
            action_ids = [action.action_id for action in card.actions]
            self.assertIn("Tap a setup action", card.summary)
            self.assertIn("Needs setup - import approved skills", rendered)
            self.assertIn("Connected through Hermes", rendered)
            self.assertIn("Temporary - deleted locally after Discord upload", rendered)
            self.assertIn("onboarding.skills.import", action_ids)
            self.assertIn("onboarding.skills.help", action_ids)
            self.assertIn("onboarding.roots.use_detected", action_ids)
            self.assertIn("onboarding.roots.help", action_ids)
            self.assertIn("orbit.retention.save", action_ids)
            self.assertNotIn("/private/detected/projects", rendered)


if __name__ == "__main__":
    unittest.main()
