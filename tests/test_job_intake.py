from __future__ import annotations

import unittest
from datetime import UTC, datetime

from recruiting_pipeline.job_intake import select_relevant_evidence
from recruiting_pipeline.models import Evidence


class JobIntakeTests(unittest.TestCase):
    def test_selects_only_approved_evidence_with_job_keyword_overlap(self) -> None:
        evidence = [
            Evidence(
                "ev1",
                "Career#Projects",
                "Built Python data pipelines for ML training.",
                True,
                datetime.now(UTC),
            ),
            Evidence(
                "ev2",
                "Career#Experience",
                "Led customer success renewals.",
                True,
                datetime.now(UTC),
            ),
            Evidence(
                "ev3", "Career#Private", "Unapproved Kubernetes work.", False, datetime.now(UTC)
            ),
        ]
        selected = select_relevant_evidence("Python machine learning engineer", evidence)
        self.assertEqual([item.id for item in selected], ["ev1"])


if __name__ == "__main__":
    unittest.main()
