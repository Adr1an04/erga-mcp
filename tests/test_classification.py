from __future__ import annotations

import unittest

from erga_mcp.classification import classify_application_message


class ClassificationTests(unittest.TestCase):
    def test_denial_language_wins_over_acknowledgement_language(self) -> None:
        result = classify_application_message(
            subject="Thank you for applying",
            preview="We will not be moving forward with your application.",
        )

        self.assertEqual(result.kind, "denial")
        self.assertTrue(result.requires_review)

    def test_clear_acknowledgement_can_be_processed_as_a_local_event(self) -> None:
        result = classify_application_message(
            subject="We received your application",
            preview="Thank you for applying to Example Systems.",
        )

        self.assertEqual(result.kind, "acknowledgement")
        self.assertFalse(result.requires_review)
        self.assertGreaterEqual(result.confidence, 0.9)

    def test_thank_you_for_interest_is_an_acknowledgement(self) -> None:
        result = classify_application_message(
            subject="Adrian, thank you for your interest in Tesla",
            preview="",
        )

        self.assertEqual(result.kind, "acknowledgement")
        self.assertFalse(result.requires_review)

    def test_assessment_invitation_requires_immediate_review(self) -> None:
        result = classify_application_message(
            subject="Your HackerRank Software Engineer Intern Coding Test Invitation",
            preview="Example Systems invites you to complete an online assessment.",
        )

        self.assertEqual(result.kind, "assessment")
        self.assertTrue(result.requires_review)
        self.assertGreaterEqual(result.confidence, 0.95)

    def test_interview_invitation_requires_immediate_review(self) -> None:
        result = classify_application_message(
            subject="Schedule your technical interview",
            preview="Choose an interview time with the engineering team.",
        )

        self.assertEqual(result.kind, "interview")
        self.assertTrue(result.requires_review)

    def test_offer_takes_precedence_over_prior_interview_language(self) -> None:
        result = classify_application_message(
            subject="Offer letter",
            preview="Following your technical interview, we are pleased to offer you the role.",
        )

        self.assertEqual(result.kind, "offer")
        self.assertTrue(result.requires_review)


if __name__ == "__main__":
    unittest.main()
