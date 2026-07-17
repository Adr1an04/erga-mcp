from __future__ import annotations

import unittest

from recruiting_pipeline.classification import classify_application_message


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


if __name__ == "__main__":
    unittest.main()
