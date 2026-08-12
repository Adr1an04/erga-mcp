from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.portfolio.skills import (
    approve_git_skill_group,
    build_git_skill_review_card,
    explicit_skills_in_texts,
    reconcile_git_skill_groups,
)
from erga_mcp.store import ErgaStore


class GitSkillTests(unittest.TestCase):
    def test_approved_text_import_requires_explicit_audited_skill_names(self) -> None:
        self.assertEqual(
            explicit_skills_in_texts(["Ready to go improve Python services with postgres."]),
            ("PostgreSQL", "Python"),
        )
        self.assertIn("Go", explicit_skills_in_texts(["Built a Go API."]))

    def _store(self, root: Path) -> ErgaStore:
        store = ErgaStore(root / "erga.sqlite3")
        store.initialize()
        return store

    def test_seeded_confirmed_unconfirmed_and_git_discovered_statuses_remain_distinct(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            store.set_skill_seeds(["React", "PostgreSQL"])
            react = store.add_git_candidate(
                repo_path="/synthetic/web",
                commit_sha="a" * 40,
                commit_range="a" * 40,
                text="Git commit: Add React component\nChanged files: package.json, src/App.tsx",
            )
            prisma = store.add_git_candidate(
                repo_path="/synthetic/api",
                commit_sha="b" * 40,
                commit_range="b" * 40,
                text=(
                    "Git commit: Add Prisma model\n"
                    "Changed files: package.json, prisma/schema.prisma"
                ),
            )
            assert react is not None and prisma is not None

            groups = {group.normalized_skill: group for group in reconcile_git_skill_groups(store)}

            self.assertEqual(groups["react"].source_status, "seeded_and_confirmed")
            self.assertTrue(groups["react"].eligible_for_approval)
            self.assertEqual(groups["postgresql"].source_status, "self_reported_unconfirmed")
            self.assertFalse(groups["postgresql"].eligible_for_approval)
            self.assertEqual(groups["prisma"].source_status, "git_discovered")
            self.assertTrue(groups["prisma"].eligible_for_approval)
            self.assertEqual(store.list_evidence(), [])

    def test_aliases_are_audited_and_words_are_not_inferred_from_arbitrary_prose(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            store.set_skill_seeds(["PostgreSQL", "React"])
            store.add_git_candidate(
                repo_path="/synthetic/api",
                commit_sha="c" * 40,
                commit_range="c" * 40,
                text="Git commit: Configure postgres adapter\nChanged files: pyproject.toml",
            )
            store.add_git_candidate(
                repo_path="/synthetic/science",
                commit_sha="d" * 40,
                commit_range="d" * 40,
                text="Git commit: Improve reactor simulation\nChanged files: reactor.py",
            )

            groups = {group.normalized_skill: group for group in reconcile_git_skill_groups(store)}

            self.assertEqual(groups["postgresql"].source_status, "seeded_and_confirmed")
            self.assertEqual(groups["react"].source_status, "self_reported_unconfirmed")

    def test_group_approval_requires_corroboration_and_explicitly_approves_candidates(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            store.set_skill_seeds(["PostgreSQL", "FastAPI"])
            candidate = store.add_git_candidate(
                repo_path="/synthetic/api",
                commit_sha="e" * 40,
                commit_range="e" * 40,
                text="Git commit: Implement FastAPI endpoint\nChanged files: src/api.py",
            )
            assert candidate is not None

            with self.assertRaisesRegex(ValueError, "corroborated"):
                approve_git_skill_group(store, "postgresql")
            approved = approve_git_skill_group(store, "fastapi")

            self.assertEqual(len(approved), 1)
            self.assertTrue(approved[0].approved)
            self.assertIn(candidate.commit_sha, approved[0].source_ref)

    def test_review_card_is_paginated_and_render_only(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            store.set_skill_seeds(["React", "PostgreSQL", "FastAPI"])
            store.add_git_candidate(
                repo_path="/synthetic/web",
                commit_sha="f" * 40,
                commit_range="f" * 40,
                text="Git commit: Add React view\nChanged files: package.json",
            )

            card = build_git_skill_review_card(store, page=2, page_size=2)

            self.assertEqual(card.page, 2)
            self.assertEqual(card.page_count, 2)
            self.assertEqual(len(card.fields), 1)
            self.assertEqual(card.actions[0].action_id, "git.scan")
            self.assertEqual(store.list_evidence(), [])

    def test_skipping_a_group_hides_it_without_approving_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            store.set_skill_seeds(["React"])

            store.set_git_skill_group_skipped("react", skipped=True)

            self.assertEqual(reconcile_git_skill_groups(store), [])
            self.assertEqual(store.list_evidence(), [])
            store.set_git_skill_group_skipped("react", skipped=False)
            self.assertEqual(reconcile_git_skill_groups(store)[0].normalized_skill, "react")


if __name__ == "__main__":
    unittest.main()
