from __future__ import annotations

import unittest

from erga_mcp.tracking.mail_receipts import parse_application_receipt


class MailReceiptTests(unittest.TestCase):
    def test_extracts_workday_requisition_company_and_role(self) -> None:
        receipt = parse_application_receipt(
            sender="acme-gpu@myworkday.com",
            subject="Thank you for your interest in Acme GPU Labs",
            content=(
                "We want to confirm that your application for the JR9000001 Acme GPU Labs "
                "Machine Learning Systems Intern role has been received."
            ),
        )

        self.assertEqual(receipt.company, "Acme GPU Labs")
        self.assertEqual(receipt.role, "Machine Learning Systems Intern")
        self.assertEqual(receipt.requisition_ids, ("jr9000001",))

    def test_extracts_eightfold_job_number_and_role(self) -> None:
        receipt = parse_application_receipt(
            sender="Example Software Careers <donotreply@email.careers.example.test>",
            subject="Thank you for your application!",
            content=(
                "Thank you for taking the time to submit your application for AI Software "
                "Engineering Intern - Edge (Job number: 900050373)."
            ),
        )

        self.assertEqual(receipt.company, "Example Software")
        self.assertEqual(receipt.role, "AI Software Engineering Intern - Edge")
        self.assertEqual(receipt.requisition_ids, ("900050373",))

    def test_extracts_company_from_ats_sender_and_role_from_receipt(self) -> None:
        receipt = parse_application_receipt(
            sender="examplepayments@myworkday.com",
            subject="Thank you for your application!",
            content=(
                "Thank you for your interest in joining Example Payments! We have received your "
                "application for the role: Software Engineer Intern, Summer 2027 – United States."
            ),
        )

        self.assertEqual(receipt.company, "Example Payments")
        self.assertEqual(receipt.role, "Software Engineer Intern, Summer 2027 – United States")

    def test_extracts_role_from_interest_template(self) -> None:
        receipt = parse_application_receipt(
            sender="exampleentertainment@myworkday.com",
            subject="Your Example Entertainment Careers Application Is In!",
            content=(
                "We are delighted by the interest you’ve shown in the Platform Software "
                "Engineering Internship, Spring 2027 position."
            ),
        )

        self.assertEqual(receipt.company, "Example Entertainment")
        self.assertEqual(receipt.role, "Platform Software Engineering Internship, Spring 2027")


if __name__ == "__main__":
    unittest.main()
