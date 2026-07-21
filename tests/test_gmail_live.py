from __future__ import annotations

import unittest
from unittest.mock import patch

from erga_mcp.integrations.gmail_live import (
    _run_gws,
    fetch_all_inbox_metadata_with_gws,
    fetch_inbox_metadata_with_gws,
    parse_message_metadata,
)


class GmailLiveTests(unittest.TestCase):
    def test_runs_windows_cmd_launchers_through_cmd_exe(self) -> None:
        command = [r"C:\tools\gws.cmd", "gmail", "users", "messages", "list"]
        with (
            patch("erga_mcp.integrations.gmail_live.os.name", "nt"),
            patch("erga_mcp.integrations.gmail_live.subprocess.run") as run,
        ):
            _run_gws(command)

        self.assertEqual(run.call_args.args[0][:4], ["cmd.exe", "/d", "/s", "/c"])
        self.assertTrue(run.call_args.args[0][4].endswith("gws.cmd gmail users messages list"))

    def test_parses_gmail_metadata_without_body_content(self) -> None:
        message = parse_message_metadata(
            {
                "id": "gmail-1",
                "internalDate": "1760000000000",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "recruiter@example.test"},
                        {"name": "Subject", "value": "Software Engineer role"},
                    ]
                },
                "snippet": "Short preview only",
            }
        )

        self.assertEqual(message.message_id, "gmail:gmail-1")
        self.assertEqual(message.sender, "recruiter@example.test")
        self.assertEqual(message.subject, "Software Engineer role")
        self.assertEqual(message.preview, "Short preview only")

    def test_reads_metadata_through_user_configured_gws(self) -> None:
        calls: list[list[str]] = []

        def run(command: list[str]) -> dict[str, object]:
            calls.append(command)
            if "list" in command:
                return {"messages": [{"id": "gmail-1"}]}
            return {
                "id": "gmail-1",
                "internalDate": "1760000000000",
                "payload": {"headers": [{"name": "From", "value": "r@example.test"}]},
            }

        messages = fetch_inbox_metadata_with_gws(gws_command="gws", limit=1, run=run)

        self.assertEqual([item.message_id for item in messages], ["gmail:gmail-1"])
        self.assertEqual(len(calls), 2)
        self.assertIn("list", calls[0])

    def test_reads_each_gmail_page_until_the_inbox_is_exhausted(self) -> None:
        calls: list[list[str]] = []
        listed = 0

        def run(command: list[str]) -> dict[str, object]:
            nonlocal listed
            calls.append(command)
            if "list" in command:
                listed += 1
                if listed == 1:
                    return {
                        "messages": [{"id": "gmail-1"}, {"id": "gmail-2"}],
                        "nextPageToken": "next",
                    }
                return {"messages": [{"id": "gmail-3"}]}
            message_id = (
                "gmail-3" if "gmail-3" in command[command.index("--params") + 1] else "gmail-1"
            )
            return {
                "id": message_id,
                "internalDate": "1760000000000",
                "payload": {"headers": []},
            }

        messages = fetch_all_inbox_metadata_with_gws(
            gws_command="gws", page_size=2, max_messages=10, run=run
        )

        self.assertEqual(len(messages), 3)
        self.assertEqual(sum("list" in call for call in calls), 2)
        self.assertIn('"pageToken": "next"', calls[-2][calls[-2].index("--params") + 1])


if __name__ == "__main__":
    unittest.main()
