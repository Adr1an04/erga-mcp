from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import erga_mcp.integrations.hermes as hermes
from erga_mcp.integrations.hermes import (
    install_hermes_monitor_scripts,
    install_hermes_update_script,
    request_hermes_gateway_restart,
    synchronize_router_plugin,
)


class CronSetupTests(unittest.TestCase):
    def test_installs_opt_in_no_agent_update_runner_without_scheduling_it(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            config.write_text("[paths]\n")
            scripts = root / "hermes" / "scripts"

            result = install_hermes_update_script(
                config_path=config,
                scripts_dir=scripts,
                python_executable=Path("/synthetic/python"),
            )

            settings = json.loads((scripts / "erga-mcp-update.json").read_text())
            runner = (scripts / "erga-mcp-update.py").read_text()
            self.assertEqual(settings["config_path"], str(config.resolve()))
            self.assertEqual(settings["hermes_home"], str((root / "hermes").resolve()))
            self.assertEqual(result["update_script"], "erga-mcp-update.py")
            self.assertEqual(result["suggested_job"]["schedule"], "*/15 * * * *")
            self.assertTrue(result["suggested_job"]["no_agent"])
            self.assertNotIn("deliver", result["suggested_job"])
            self.assertIn('"update"', runner)
            self.assertIn('"--scheduled"', runner)
            self.assertNotIn("cron", runner.casefold())

    def test_synchronizes_only_the_known_router_plugin_files(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "checkout" / "integrations/hermes/plugins/erga-mcp-router"
            target = root / "hermes" / "plugins" / "erga-mcp-router"
            source.mkdir(parents=True)
            target.mkdir(parents=True)
            for name, content in {
                "plugin.yaml": 'name: erga-mcp-router\nversion: "2"\n',
                "__init__.py": "NEW = True\n",
                "after-install.md": "new docs\n",
            }.items():
                (source / name).write_text(content)
            (target / "plugin.yaml").write_text('name: erga-mcp-router\nversion: "1"\n')
            (target / "__init__.py").write_text("NEW = False\n")
            (target / "after-install.md").write_text("old docs\n")
            (target / "local-note.txt").write_text("preserve me\n")

            changed = synchronize_router_plugin(
                checkout_root=root / "checkout",
                hermes_home=root / "hermes",
            )

            self.assertTrue(changed)
            self.assertEqual((target / "__init__.py").read_text(), "NEW = True\n")
            self.assertEqual((target / "local-note.txt").read_text(), "preserve me\n")
            self.assertFalse(
                synchronize_router_plugin(
                    checkout_root=root / "checkout",
                    hermes_home=root / "hermes",
                )
            )

    def test_gateway_restart_is_profile_scoped_and_detached(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def launch(command: list[str], **kwargs: object) -> object:
            calls.append((command, kwargs))
            return object()

        with (
            TemporaryDirectory() as directory,
            patch("erga_mcp.integrations.hermes.shutil.which", return_value="/bin/hermes"),
            patch.dict(
                os.environ,
                {"PYTHONHOME": "/poison", "PYTHONPATH": "/poison", "VIRTUAL_ENV": "/poison"},
            ),
        ):
            requested = request_hermes_gateway_restart(
                hermes_home=Path(directory),
                launcher=launch,  # type: ignore[arg-type]
            )

        self.assertTrue(requested)
        self.assertEqual(calls[0][0], ["/bin/hermes", "gateway", "restart"])
        self.assertEqual(calls[0][1]["env"]["HERMES_HOME"], str(Path(directory).resolve()))
        self.assertNotIn("PYTHONHOME", calls[0][1]["env"])
        self.assertNotIn("PYTHONPATH", calls[0][1]["env"])
        self.assertNotIn("VIRTUAL_ENV", calls[0][1]["env"])

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
