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
        "erga_mcp.integrations.discord.bridge",
        "erga_mcp.integrations.discord.cards",
        "erga_mcp.integrations.discord.setup",
        "erga_mcp.mcp.transport",
        "erga_mcp.mcp.server",
        "erga_mcp.operations.uninstall",
    }
)
_INTERFACE_ROOTS = ("erga_mcp.mcp",)
_THIRD_PARTY_INTERFACE_ROOTS = ("mcp",)
_EXPECTED_TOP_LEVEL_MODULES = {
    "__init__.py",
    "cli.py",
    "config.py",
    "job_urls.py",
    "models.py",
    "store.py",
    "versioning.py",
}
_EXPECTED_DOMAIN_PACKAGES = {
    "applications",
    "operations",
    "portfolio",
    "resumes",
    "tracking",
}
_EXPECTED_TOP_LEVEL_PACKAGES = _EXPECTED_DOMAIN_PACKAGES | {"integrations", "mcp"}


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


def _local_import_modules(path: Path) -> set[str]:
    module = _module_name(path)
    package_parts = module.split(".") if path.name == "__init__.py" else module.split(".")[:-1]
    imports: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = package_parts[: len(package_parts) - node.level + 1]
                suffix = node.module.split(".") if node.module else []
                target = ".".join((*base, *suffix))
                if node.module and target.startswith("erga_mcp"):
                    imports.add(target)
                    if _is_local_package(target):
                        imports.update(
                            f"{target}.{alias.name}" for alias in node.names if alias.name != "*"
                        )
                elif not node.module:
                    imports.update(
                        f"{target}.{alias.name}" for alias in node.names if alias.name != "*"
                    )
            elif node.module and node.module.startswith("erga_mcp"):
                imports.add(node.module)
                if _is_local_package(node.module):
                    imports.update(
                        f"{node.module}.{alias.name}" for alias in node.names if alias.name != "*"
                    )
        elif isinstance(node, ast.Import):
            imports.update(name.name for name in node.names if name.name.startswith("erga_mcp"))
    return imports


def _is_local_package(module: str) -> bool:
    parts = module.split(".")
    if not parts or parts[0] != "erga_mcp":
        return False
    return (_PACKAGE.joinpath(*parts[1:]) / "__init__.py").is_file()


def _initializer_attributes(paths: list[Path]) -> set[str]:
    attributes: set[str] = set()
    for path in paths:
        if path.name != "__init__.py":
            continue
        module = _module_name(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            names: list[str] = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.append(node.name)
            elif isinstance(node, ast.Assign):
                names.extend(target.id for target in node.targets if isinstance(target, ast.Name))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.append(node.target.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                names.extend(alias.asname or alias.name.split(".", 1)[0] for alias in node.names)
            attributes.update(f"{module}.{name}" for name in names)
    return attributes


class ArchitectureTests(unittest.TestCase):
    def test_import_parser_resolves_absolute_import_from(self) -> None:
        imports = _imports_from_source(
            "from erga_mcp.mcp.server import build_server\n",
            module="erga_mcp.example",
        )

        self.assertEqual(
            imports,
            {"erga_mcp.mcp.server", "erga_mcp.mcp.server.build_server"},
        )

    def test_import_parser_resolves_package_relative_alias(self) -> None:
        imports = _imports_from_source(
            "from .mcp import server\n",
            module="erga_mcp.example",
        )

        self.assertEqual(imports, {"erga_mcp.mcp", "erga_mcp.mcp.server"})

    def test_package_root_contains_only_stable_foundation_modules(self) -> None:
        modules = {path.name for path in _PACKAGE.glob("*.py")}

        self.assertEqual(modules, _EXPECTED_TOP_LEVEL_MODULES)

    def test_top_level_packages_are_explicit_and_documented(self) -> None:
        actual_packages = {
            path.name
            for path in _PACKAGE.iterdir()
            if path.is_dir() and (path / "__init__.py").is_file()
        }
        self.assertEqual(actual_packages, _EXPECTED_TOP_LEVEL_PACKAGES)

        for package_name in _EXPECTED_TOP_LEVEL_PACKAGES:
            package = _PACKAGE / package_name
            with self.subTest(package=package_name):
                self.assertTrue((package / "__init__.py").is_file())
                self.assertTrue((package / "AGENTS.md").is_file())

    def test_package_initializers_do_not_hide_compatibility_reexports(self) -> None:
        violations: list[str] = []
        for path in _PACKAGE.rglob("__init__.py"):
            if path == _PACKAGE / "__init__.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            statements = [
                node
                for node in tree.body
                if not (
                    isinstance(node, ast.Expr)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                )
            ]
            if statements:
                violations.append(str(path.relative_to(_ROOT)))
        self.assertEqual(violations, [])

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

    def test_integrations_do_not_import_application_workflows(self) -> None:
        violations = [
            f"{path.relative_to(_ROOT)} -> {imported}"
            for path in (_PACKAGE / "integrations").rglob("*.py")
            for imported in sorted(_local_imports(path))
            if imported == "erga_mcp.applications" or imported.startswith("erga_mcp.applications.")
        ]

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

    def test_local_import_targets_exist(self) -> None:
        paths = list(_PACKAGE.rglob("*.py"))
        modules = {_module_name(path) for path in paths}
        initializer_attributes = _initializer_attributes(paths)
        missing = [
            f"{path.relative_to(_ROOT)} -> {imported}"
            for path in paths
            for imported in sorted(_local_import_modules(path))
            if imported not in modules and imported not in initializer_attributes
        ]

        self.assertEqual(missing, [])

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
