from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_mcp_server_runtime_dependencies_are_direct_and_not_optional(self) -> None:
        project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        dependencies = project["project"]["dependencies"]
        optional = project["project"].get("optional-dependencies", {})

        self.assertIn("mcp>=1.28,<2", dependencies)
        self.assertIn("uvicorn>=0.30,<1", dependencies)
        self.assertNotIn("mcp", optional)
        self.assertEqual(project["project"]["scripts"]["erga-mcp"], "erga_mcp.mcp_server:main")

    def test_mcp_metadata_and_documentation_inventory_stay_aligned(self) -> None:
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        contribution_docs = (PROJECT_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        client_docs = (PROJECT_ROOT / "docs" / "mcp-clients.md").read_text(encoding="utf-8")
        security_docs = (PROJECT_ROOT / "docs" / "security.md").read_text(encoding="utf-8")
        getting_started = (PROJECT_ROOT / "docs" / "getting-started.md").read_text(encoding="utf-8")
        ci_workflow = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        release_workflow = (PROJECT_ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("docs/mcp-clients.md", readme)
        self.assertIn("`mcp` Python runtime dependency", client_docs)
        self.assertIn("`uvicorn`", client_docs)
        self.assertIn("`erga-mcp` package", client_docs)
        self.assertNotIn("--extra mcp", contribution_docs)
        self.assertNotIn("--extra mcp", readme)
        self.assertNotIn("--extra mcp", getting_started)
        self.assertNotIn("--extra mcp", ci_workflow)
        self.assertNotIn("--extra mcp", release_workflow)
        self.assertNotIn("[mcp]", ci_workflow)
        self.assertIn("tests.test_mcp_interoperability", contribution_docs)
        self.assertIn("Streamable HTTP", client_docs)
        self.assertIn("Claude Desktop and Cursor", client_docs)
        self.assertIn("Claude Code", client_docs)
        self.assertIn("Codex", client_docs)
        self.assertIn("VS Code", client_docs)
        self.assertIn("OpenClaw", client_docs)
        self.assertIn("Streamable HTTP", security_docs)


if __name__ == "__main__":
    unittest.main()
