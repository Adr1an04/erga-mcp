from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from erga_mcp.git_evidence import GitCommit
from erga_mcp.git_project_enrichment import (
    enrich_ranked_projects_from_git,
    merge_github_project_catalogue,
)
from erga_mcp.github_projects import GitHubProject
from erga_mcp.models import Evidence, GitChangeObservation
from erga_mcp.project_inventory import ProjectCandidate
from erga_mcp.store import ErgaStore


def _candidate(
    project_id: str, title: str, tags: tuple[str, ...], repositories: tuple[str, ...]
) -> ProjectCandidate:
    evidence_id = f"ev_{project_id}"
    return ProjectCandidate(
        id=project_id,
        title=title,
        latex=(
            rf"\resumeProjectHeading{{\textbf{{{title}}}}}{{}}"
            "\n"
            r"\resumeItemListStart"
            "\n"
            rf"\resumeItem{{Built the approved {title} baseline.}}"
            "\n"
            r"\resumeItemListEnd"
        ),
        evidence_ids=(evidence_id,),
        bullet_evidence_ids=((evidence_id,),),
        tags=tags,
        git_repositories=repositories,
    )


class GitProjectEnrichmentTests(unittest.TestCase):
    def test_merges_discovered_github_metadata_without_duplicating_curated_repositories(
        self,
    ) -> None:
        curated = (
            _candidate(
                "api-platform",
                "API Platform",
                ("python", "api"),
                ("example/api-platform",),
            ),
        )
        discovered = (
            GitHubProject(
                "example/api-platform",
                "api-platform",
                "Duplicate curated project",
                "Python",
                ("api",),
            ),
            GitHubProject(
                "example/worker-runtime",
                "worker-runtime",
                "Distributed worker orchestration",
                "Rust",
                ("distributed-systems",),
            ),
        )

        merged = merge_github_project_catalogue(curated, discovered)

        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[1].title, "Worker Runtime")
        self.assertEqual(merged[1].git_repositories, ("example/worker-runtime",))
        self.assertEqual(merged[1].evidence_ids, ())
        self.assertIn("distributed-systems", merged[1].tags)

    def test_matches_a_discovered_repository_to_a_curated_project_by_stable_identity(self) -> None:
        curated = (_candidate("ctrl-arm", "Ctrl-ARM", ("c++", "python"), ()),)
        discovered = (
            GitHubProject(
                "example/ctrl-arm",
                "ctrl-arm",
                "Real-time controller",
                "C++",
                ("real-time",),
            ),
        )

        merged = merge_github_project_catalogue(curated, discovered)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].git_repositories, ("example/ctrl-arm",))
        self.assertIn("real-time", merged[0].tags)

    def test_ranks_json_but_keeps_git_research_out_of_resume_copy(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "api-platform"
            repo.mkdir()
            store = ErgaStore(root / "state.sqlite3")
            approved = Evidence(
                "ev_api-platform",
                "approved:api-platform",
                "Built the approved API Platform baseline.",
                True,
                datetime.now(UTC),
            )
            store.add_evidence(
                source_ref=approved.source_ref,
                text=approved.text,
                approved=True,
            )
            candidates = (
                _candidate(
                    "frontend",
                    "Frontend",
                    ("react", "css"),
                    ("example/frontend",),
                ),
                _candidate(
                    "api-platform",
                    "API Platform",
                    ("python", "api", "testing"),
                    ("example/api-platform",),
                ),
            )
            commits = [GitCommit("a" * 40, (), "api work", ("src/api/routes.py",))]
            observations = [
                GitChangeObservation(
                    repo_path=str(repo),
                    commit_sha="a" * 40,
                    files=["src/api/routes.py", "tests/api/test_routes.py"],
                    additions=120,
                    deletions=18,
                    symbols=["create_job", "POST /jobs"],
                    change_kinds=["API", "testing"],
                    diff_hash="d" * 64,
                )
            ]

            with (
                patch(
                    "erga_mcp.git_project_enrichment.connected_github_login",
                    return_value="sample-user",
                ),
                patch(
                    "erga_mcp.git_project_enrichment.ensure_github_worktree",
                    return_value=repo,
                ),
                patch(
                    "erga_mcp.git_project_enrichment.github_authored_commit_shas",
                    return_value={"a" * 40},
                ),
                patch(
                    "erga_mcp.git_project_enrichment.scan_authored_commits",
                    return_value=commits,
                ),
                patch(
                    "erga_mcp.git_project_enrichment.analyze_commits",
                    return_value=observations,
                ),
            ):
                result = enrich_ranked_projects_from_git(
                    candidates=candidates,
                    job_description="Required: Python API testing",
                    project_count=1,
                    bullets_per_project=1,
                    bullet_min_characters=99,
                    bullet_target_characters=105,
                    bullet_max_characters=116,
                    store=store,
                    cache_root=root / "cache",
                )

        self.assertEqual(result.catalogue_candidate_count, 2)
        self.assertEqual([item.id for item in result.candidates], ["frontend", "api-platform"])
        selected = next(item for item in result.candidates if item.id == "api-platform")
        self.assertIn("approved API Platform baseline", selected.latex)
        self.assertNotIn("commit", selected.latex)
        self.assertNotIn("+120/-18 lines", selected.latex)
        self.assertTrue(all(105 <= len(item.text) <= 116 for item in result.evidence))
        self.assertEqual(len(result.evidence), 1)
        self.assertTrue(result.evidence[0].approved)
        self.assertTrue(result.evidence[0].source_ref.startswith("git-derived:api-platform@"))
        self.assertEqual(result.reports[0]["authored_commits"], 1)
        self.assertEqual(result.reports[0]["resume_bullets_source"], "approved_catalogue")
        self.assertEqual(result.warnings, ())

    def test_missing_repository_mapping_retains_approved_catalogue_bullet(self) -> None:
        with TemporaryDirectory() as directory:
            result = enrich_ranked_projects_from_git(
                candidates=(_candidate("offline-tool", "Offline Tool", ("python",), ()),),
                job_description="Python",
                project_count=1,
                bullets_per_project=1,
                bullet_min_characters=0,
                bullet_target_characters=0,
                bullet_max_characters=0,
                store=ErgaStore(Path(directory) / "state.sqlite3"),
                cache_root=Path(directory) / "cache",
            )

        self.assertEqual(result.candidates[0].id, "offline-tool")
        self.assertIn("approved Offline Tool baseline", result.candidates[0].latex)
        self.assertIn("no git_repositories mapping", result.warnings[0])
        self.assertEqual(result.reports[0]["project_id"], "offline-tool")
        self.assertEqual(result.reports[0]["status"], "unmapped")

    def test_exact_resume_selection_overrides_an_independent_git_rerank(self) -> None:
        candidates = (
            _candidate("frontend", "Frontend", ("react",), ()),
            _candidate("python-api", "Python API", ("python", "api"), ()),
        )
        with TemporaryDirectory() as directory:
            result = enrich_ranked_projects_from_git(
                candidates=candidates,
                job_description="Required: Python API",
                project_count=1,
                bullets_per_project=1,
                bullet_min_characters=0,
                bullet_target_characters=0,
                bullet_max_characters=0,
                store=ErgaStore(Path(directory) / "state.sqlite3"),
                cache_root=Path(directory) / "cache",
                selected_project_ids=("frontend",),
            )

        self.assertEqual([report["project_id"] for report in result.reports], ["frontend"])


if __name__ == "__main__":
    unittest.main()
