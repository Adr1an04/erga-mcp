from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.config import ContactOutputSettings
from erga_mcp.models import RecruiterContact
from erga_mcp.tracking.contact_projection import project_recruiter_contacts


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
            path = Path(directory) / "jane.smith-example.test.md"
            existing = path.read_text(encoding="utf-8")
            path.write_text(existing + "\nMy private notes.\n", encoding="utf-8")
            project_recruiter_contacts([contact], [output])
            rendered = path.read_text(encoding="utf-8")

        self.assertIn("My private notes.", rendered)
        self.assertEqual(rendered.count("<!-- erga:recruiter-contact:start -->"), 1)
        self.assertIn("jane.smith@example.test", rendered)

    def test_writes_distinct_notes_for_contacts_with_the_same_name(self) -> None:
        contacts = [
            RecruiterContact(
                id=f"contact_{index}",
                email=f"jane.smith{index}@example.test",
                name="Jane Smith",
                company=None,
                first_seen_at=datetime(2026, 7, 1, tzinfo=UTC),
                last_seen_at=datetime(2026, 7, 2, tzinfo=UTC),
                source_message_id=f"message-{index}",
            )
            for index in (1, 2)
        ]
        with TemporaryDirectory() as directory:
            output = ContactOutputSettings(kind="obsidian", directory=Path(directory))
            self.assertEqual(project_recruiter_contacts(contacts, [output]), 2)
            notes = sorted(Path(directory).glob("*.md"))
            rendered = [path.read_text(encoding="utf-8") for path in notes]

        self.assertEqual(len(notes), 2)
        self.assertIn("jane.smith1@example.test", rendered[0] + rendered[1])
        self.assertIn("jane.smith2@example.test", rendered[0] + rendered[1])


if __name__ == "__main__":
    unittest.main()
