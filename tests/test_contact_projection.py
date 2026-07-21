from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.config import ContactOutputSettings
from erga_mcp.contact_projection import project_recruiter_contacts
from erga_mcp.models import RecruiterContact


class ContactProjectionTests(unittest.TestCase):
    def test_preserves_user_content_while_updating_managed_contact_metadata(self) -> None:
        contact = RecruiterContact(
            id="contact_1",
            email="jane.smith@example.test",
            name="Jane Smith",
            company=None,
            first_seen_at=datetime(2026, 7, 1, tzinfo=UTC),
            last_seen_at=datetime(2026, 7, 2, tzinfo=UTC),
            source_message_id="message-1",
        )
        with TemporaryDirectory() as directory:
            output = ContactOutputSettings(kind="obsidian", directory=Path(directory))
            project_recruiter_contacts([contact], [output])
            path = Path(directory) / "Jane Smith.md"
            existing = path.read_text(encoding="utf-8")
            path.write_text(existing + "\nMy private notes.\n", encoding="utf-8")
            project_recruiter_contacts([contact], [output])
            rendered = path.read_text(encoding="utf-8")

        self.assertIn("My private notes.", rendered)
        self.assertEqual(rendered.count("<!-- erga:recruiter-contact:start -->"), 1)
        self.assertIn("jane.smith@example.test", rendered)


if __name__ == "__main__":
    unittest.main()
