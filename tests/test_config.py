from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from recruiting_pipeline.config import load_config


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


if __name__ == "__main__":
    unittest.main()
