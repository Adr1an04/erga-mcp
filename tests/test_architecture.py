from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

_ROOT = Path(__file__).parents[1]
_PACKAGE = _ROOT / "src" / "erga_mcp"
_INTERFACE_MODULES = frozenset(
    {
        "erga_mcp.cli",
        "erga_mcp.discord_bridge",
        "erga_mcp.discord_cards",
        "erga_mcp.discord_setup",
        "erga_mcp.http_transport",
        "erga_mcp.mcp_server",
        "erga_mcp.uninstall",
    }
)
_INTERFACE_ROOTS = ("erga_mcp.mcp",)
_THIRD_PARTY_INTERFACE_ROOTS = ("mcp",)


def _module_name(path: Path) -> str:
    relative = path.relative_to(_PACKAGE).with_suffix("")
    parts = relative.parts[:-1] if relative.name == "__init__" else relative.parts
    return ".".join(("erga_mcp", *parts))


def _imports_from_source(source: str, *, module: str, is_package: bool = False) -> set[str]:
    package_parts = module.split(".") if is_package else module.split(".")[:-1]
    imports: set[str] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = package_parts[: len(package_parts) - node.level + 1]
                suffix = node.module.split(".") if node.module else []
                target_parts = (*base, *suffix)
                if target_parts:
                    target = ".".join(target_parts)
                    imports.add(target)
                    imports.update(
                        f"{target}.{alias.name}" for alias in node.names if alias.name != "*"
                    )
            elif node.module and (
                node.module.startswith("erga_mcp")
                or node.module.split(".", 1)[0] in _THIRD_PARTY_INTERFACE_ROOTS
            ):
                imports.add(node.module)
                imports.update(
                    f"{node.module}.{alias.name}" for alias in node.names if alias.name != "*"
                )
        elif isinstance(node, ast.Import):
            imports.update(
                name.name
                for name in node.names
                if name.name.startswith("erga_mcp.")
                or name.name.split(".", 1)[0] in _THIRD_PARTY_INTERFACE_ROOTS
            )
    return imports


def _is_interface_import(module: str) -> bool:
    roots = (*_INTERFACE_ROOTS, *_THIRD_PARTY_INTERFACE_ROOTS)
    return module in _INTERFACE_MODULES or any(
        module == root or module.startswith(f"{root}.") for root in roots
    )


def _local_imports(path: Path) -> set[str]:
    return _imports_from_source(
        path.read_text(encoding="utf-8"),
        module=_module_name(path),
        is_package=path.name == "__init__.py",
    )


class ArchitectureTests(unittest.TestCase):
    def test_import_parser_resolves_absolute_import_from(self) -> None:
        imports = _imports_from_source(
            "from erga_mcp.mcp_server import build_server\n",
            module="erga_mcp.example",
        )

        self.assertEqual(
            imports,
            {"erga_mcp.mcp_server", "erga_mcp.mcp_server.build_server"},
        )

    def test_import_parser_resolves_package_relative_alias(self) -> None:
        imports = _imports_from_source(
            "from . import mcp_server\n",
            module="erga_mcp.example",
        )

        self.assertEqual(imports, {"erga_mcp", "erga_mcp.mcp_server"})

    def test_import_parser_resolves_alias_under_named_relative_package(self) -> None:
        imports = _imports_from_source(
            "from .mcp import profiles\n",
            module="erga_mcp.example",
        )

        self.assertEqual(imports, {"erga_mcp.mcp", "erga_mcp.mcp.profiles"})
        self.assertTrue(any(_is_interface_import(module) for module in imports))

    def test_import_parser_marks_third_party_mcp_sdk_as_an_interface(self) -> None:
        imports = _imports_from_source(
            "from mcp.types import Tool\n",
            module="erga_mcp.example",
        )

        self.assertEqual(imports, {"mcp.types", "mcp.types.Tool"})
        self.assertTrue(any(_is_interface_import(module) for module in imports))

    def test_import_parser_exposes_named_relative_module_to_cycle_graph(self) -> None:
        imports = _imports_from_source(
            "from .package import module\n",
            module="erga_mcp.example",
        )
        available_modules = {"erga_mcp.package", "erga_mcp.package.module"}

        self.assertEqual(imports & available_modules, available_modules)

    def test_core_modules_do_not_import_optional_interfaces(self) -> None:
        violations: list[str] = []
        for path in _PACKAGE.rglob("*.py"):
            module = _module_name(path)
            if _is_interface_import(module):
                continue
            for imported in _local_imports(path):
                if _is_interface_import(imported):
                    violations.append(f"{path.name} -> {imported}")
        self.assertEqual(violations, [])

    def test_local_import_graph_has_no_cycles(self) -> None:
        paths = list(_PACKAGE.rglob("*.py"))
        modules = {_module_name(path): path for path in paths}
        edges = {
            module: {name for name in _local_imports(path) if name in modules}
            for module, path in modules.items()
        }
        visiting: list[str] = []
        visited: set[str] = set()

        def visit(module: str) -> None:
            if module in visiting:
                cycle = " -> ".join((*visiting[visiting.index(module) :], module))
                self.fail(f"local import cycle: {cycle}")
            if module in visited:
                return
            visiting.append(module)
            for dependency in sorted(edges[module]):
                visit(dependency)
            visiting.pop()
            visited.add(module)

        for module in sorted(modules):
            visit(module)

    def test_local_markdown_links_resolve(self) -> None:
        missing: list[str] = []
        documents = [
            _ROOT / "README.md",
            _ROOT / "CONTRIBUTING.md",
            _ROOT / "AGENTS.md",
            *(_ROOT / "docs").rglob("*.md"),
        ]
        for document in documents:
            text = document.read_text(encoding="utf-8")
            for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
                path_text = target.split("#", 1)[0]
                if not path_text or "://" in path_text or path_text.startswith("mailto:"):
                    continue
                if not (document.parent / path_text).resolve().exists():
                    missing.append(f"{document.relative_to(_ROOT)} -> {target}")
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
