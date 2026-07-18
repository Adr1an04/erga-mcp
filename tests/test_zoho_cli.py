from __future__ import annotations

import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from recruiting_pipeline.cli import main


class ZohoCliTests(unittest.TestCase):
    def test_connect_does_not_require_pipeline_config(self) -> None:
        output = StringIO()
        with (
            patch(
                "recruiting_pipeline.cli._read_client_secret_from_keychain", return_value="secret"
            ),
            patch(
                "recruiting_pipeline.cli.connect",
                return_value={"refresh_token": "token"},
            ),
            redirect_stdout(output),
        ):
            exit_code = main(["zoho", "connect", "--client-id", "client-id"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output.getvalue())["connected"], True)

    def test_ingests_a_local_fixture_without_any_oauth_configuration(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "zoho_messages.json"
        with TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            main(["init", "--config", str(config)])
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(
                    ["zoho", "ingest-fixture", "--config", str(config), "--fixture", str(fixture)]
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(output.getvalue()), {"created": 2})


if __name__ == "__main__":
    unittest.main()
