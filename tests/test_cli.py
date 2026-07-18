from __future__ import annotations

import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from recruiting_pipeline.cli import main
from recruiting_pipeline.config import load_config


class CliTests(unittest.TestCase):
    def test_init_creates_a_non_secret_config_and_local_database(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config" / "config.toml"

            exit_code = main(["init", "--config", str(config_path)])

            config = load_config(config_path)
            self.assertEqual(exit_code, 0)
            self.assertTrue(config_path.exists())
            self.assertTrue((config.data_dir / "pipeline.sqlite3").exists())
            self.assertNotIn("token", config_path.read_text().lower())

    def test_status_includes_mail_event_count(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            main(["init", "--config", str(config_path)])
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(["status", "--config", str(config_path)])

            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(output.getvalue())["mail_events"], 0)


if __name__ == "__main__":
    unittest.main()
