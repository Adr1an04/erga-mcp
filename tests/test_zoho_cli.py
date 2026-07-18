from __future__ import annotations

import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from recruiting_pipeline.cli import main


class ZohoCliTests(unittest.TestCase):
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
