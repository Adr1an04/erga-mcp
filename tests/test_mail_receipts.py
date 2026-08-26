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
        self.assertEqual(receipt.recruiting_cycle_hints, ("Summer 2027",))

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

    def test_subject_role_does_not_replace_the_company(self) -> None:
        receipt = parse_application_receipt(
            sender="careers@recruitment.example-financial.test",
            subject=("Thank you for applying to Engineering Internship - Metro, ST - 90001234"),
            content=(
                "Thank you for exploring new career opportunities with Example Financial! "
                "Your application for the position of Engineering Internship - Metro, ST "
                "has been received."
            ),
        )

        self.assertEqual(receipt.company, "Example Financial")
        self.assertEqual(receipt.role, "Engineering Internship - Metro, ST")

    def test_extracts_company_and_role_from_successful_submission_subject(self) -> None:
        receipt = parse_application_receipt(
            sender="talent@example.test",
            subject=(
                "You have successfully submitted your Example Computing job application - "
                "900123 - Software Developer Intern 2027"
            ),
        )

        self.assertEqual(receipt.company, "Example Computing")
        self.assertEqual(receipt.role, "Software Developer Intern 2027")
        self.assertEqual(receipt.recruiting_cycle_hints, ())

    def test_reads_cycle_from_full_body_instead_of_guessing_from_role_year(self) -> None:
        receipt = parse_application_receipt(
            sender="careers@example.test",
            subject="Application received",
            content=(
                "We received your application for 2027 Internships: Systems Engineering. "
                "This application is for our Summer 2027 university recruiting cycle."
            ),
        )

        self.assertEqual(receipt.role, "2027 Internships: Systems Engineering")
        self.assertEqual(receipt.recruiting_cycle_hints, ("Summer 2027",))

    def test_greenhouse_relay_uses_subject_company_and_body_role(self) -> None:
        receipt = parse_application_receipt(
            sender="no-reply@us.greenhouse-mail.io",
            subject="Thank you for applying to Example Security",
            content=(
                "We have received your application for the Software Engineering Intern, "
                "Summer 2027 position at Example Security."
            ),
        )

        self.assertEqual(receipt.company, "Example Security")
        self.assertEqual(receipt.role, "Software Engineering Intern, Summer 2027")

    def test_smartrecruiters_relay_does_not_become_the_company(self) -> None:
        receipt = parse_application_receipt(
            sender="notification@smartrecruiters.com",
            subject="Thank you for applying to Example Storage",
            content=(
                "We received your application for the Firmware Engineering Intern role "
                "at Example Storage."
            ),
        )

        self.assertEqual(receipt.company, "Example Storage")
        self.assertEqual(receipt.role, "Firmware Engineering Intern")

    def test_company_domain_ignores_io_tld_and_common_hq_suffix(self) -> None:
        io_receipt = parse_application_receipt(
            sender="notifications@example-security.io",
            subject="Application received",
            content="We received your application for the Software Engineering Intern role.",
        )
        hq_receipt = parse_application_receipt(
            sender="notifications@exampledatahq.com",
            subject="Application received",
            content="We received your application for the Platform Engineering Intern role.",
        )

        self.assertEqual(io_receipt.company, "Example Security")
        self.assertEqual(hq_receipt.company, "Exampledata")


if __name__ == "__main__":
    unittest.main()
