from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import erga_mcp.integrations.hermes as hermes
from erga_mcp.integrations.hermes import install_hermes_monitor_scripts


class CronSetupTests(unittest.TestCase):
    def test_installs_portable_no_agent_scripts_without_credentials(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            config.write_text('[mail]\nprovider = "gmail"\n')
            scripts = root / "hermes" / "scripts"

            result = install_hermes_monitor_scripts(
                config_path=config,
                scripts_dir=scripts,
                python_executable=Path("/synthetic/python"),
            )

            settings = json.loads((scripts / "erga-mcp-monitor.json").read_text())
            self.assertEqual(settings["config_path"], str(config.resolve()))
            self.assertEqual(
                settings["module_root"],
                str(Path(hermes.__file__).resolve().parents[2]),
            )
            self.assertNotIn("token", json.dumps(settings).casefold())
            self.assertEqual(result["suggested_jobs"][0]["deliver"], "origin")
            self.assertIn(
                "MODE = 'mail'",
                (scripts / "erga-mcp-mail.py").read_text(),
            )

    def test_installed_mail_runner_emits_an_actionable_message_for_hermes_delivery(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fake_gws_script = root / "fake-gws.py"
            fake_gws_script.write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                "if 'list' in sys.argv:\n"
                "    print(json.dumps({'messages': [{'id': 'one'}]}))\n"
                "else:\n"
                "    print(json.dumps({\n"
                "        'id': 'one',\n"
                "        'internalDate': '1784428800000',\n"
                "        'snippet': 'Choose a technical interview time.',\n"
                "        'payload': {'headers': [\n"
                "            {'name': 'From', 'value': 'recruiting@example.test'},\n"
                "            {'name': 'Subject', 'value': 'Schedule your interview'}\n"
                "        ]}\n"
                "    }))\n",
                encoding="utf-8",
            )
            if os.name == "nt":
                fake_gws = root / "fake-gws.cmd"
                fake_gws.write_text(
                    f'@echo off\r\ncall "{sys.executable}" "{fake_gws_script}" %*\r\n',
                    encoding="utf-8",
                )
            else:
                fake_gws = fake_gws_script
                fake_gws.chmod(0o755)
            config = root / "config.toml"
            config.write_text(
                '[paths]\ndata_dir = "state"\n'
                '[mail]\nprovider = "gmail"\n'
                f"gws_command = {json.dumps(str(fake_gws))}\n",
                encoding="utf-8",
            )
            scripts = root / "hermes" / "scripts"
            install_hermes_monitor_scripts(
                config_path=config,
                scripts_dir=scripts,
                python_executable=Path(sys.executable),
            )
            poisoned_packages = root / "poisoned-packages"
            poisoned_scrapling = poisoned_packages / "scrapling"
            poisoned_scrapling.mkdir(parents=True)
            (poisoned_scrapling / "__init__.py").write_text("", encoding="utf-8")
            (poisoned_scrapling / "parser.py").write_text(
                'raise ImportError("inherited PYTHONPATH was used")\n',
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(poisoned_packages)

            completed = subprocess.run(
                [sys.executable, str(scripts / "erga-mcp-mail.py")],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
                env=environment,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
            self.assertIn("Interview invitation", completed.stdout)
            self.assertIn("Schedule your interview", completed.stdout)


if __name__ == "__main__":
    unittest.main()
