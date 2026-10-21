"""P5-T8: business modules have no import path to a provider adapter (static check).

The check parses every module under `src/velora`, builds the import graph (including
`from x import y` where y is a submodule and imports inside functions), and fails if any
module outside the composition roots can reach `velora.messaging.providers`, directly or
through other modules. It also refuses any third-party OpenWA client outside the adapters.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
ADAPTERS = "velora.messaging.providers"
# The only modules allowed to wire adapters in.
COMPOSITION_ROOTS = frozenset({"velora.messaging.registry", "velora.workers.__main__"})


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(SRC).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _is_package(path: Path) -> bool:
    return path.name == "__init__.py"


def import_graph(src: Path = SRC) -> dict[str, set[str]]:
    files = {_module_name(p): p for p in src.rglob("*.py")}
    graph: dict[str, set[str]] = {}
    for name, path in files.items():
        package = name if _is_package(path) else name.rpartition(".")[0]
        edges: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                edges.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    anchor = package.split(".")
                    anchor = anchor[: len(anchor) - (node.level - 1)]
                    base = ".".join([*anchor, base] if base else anchor)
                edges.add(base)
                edges.update(f"{base}.{alias.name}" for alias in node.names)
        # Importing a.b.c also runs a and a.b.
        expanded = set()
        for target in edges:
            pieces = target.split(".")
            expanded.update(".".join(pieces[:i]) for i in range(1, len(pieces) + 1))
        graph[name] = {t for t in expanded if t in files and t != name}
    return graph


def is_adapter(module: str) -> bool:
    return module == ADAPTERS or module.startswith(ADAPTERS + ".")


def violations(graph: Mapping[str, set[str]]) -> dict[str, list[str]]:
    """Each non-root, non-adapter module that can reach an adapter, with one import path."""
    found: dict[str, list[str]] = {}
    for start in graph:
        if start in COMPOSITION_ROOTS or is_adapter(start) or start == "velora":
            continue
        parents: dict[str, str | None] = {start: None}
        queue = [start]
        while queue:
            current = queue.pop(0)
            if is_adapter(current):
                path = [current]
                while (parent := parents[path[-1]]) is not None:
                    path.append(parent)
                found[start] = list(reversed(path))
                break
            for nxt in sorted(graph.get(current, ())):
                if nxt not in parents and nxt not in COMPOSITION_ROOTS:
                    parents[nxt] = current
                    queue.append(nxt)
    return found


def test_business_modules_have_no_import_path_to_a_provider_adapter() -> None:
    graph = import_graph()
    assert "velora.messaging.providers.fake" in graph  # the check sees the adapters
    assert violations(graph) == {}


def test_composition_roots_are_imported_by_nothing_but_entry_points() -> None:
    graph = import_graph()
    importers = {m for m, targets in graph.items() if targets & COMPOSITION_ROOTS}
    assert importers <= COMPOSITION_ROOTS


def test_no_openwa_client_is_imported_outside_the_adapters() -> None:
    for path in SRC.rglob("*.py"):
        name = _module_name(path)
        if is_adapter(name):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            modules = []
            if isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            assert not any("openwa" in m.lower() for m in modules), (name, modules)


@pytest.mark.parametrize(
    ("graph", "offender"),
    [
        # Direct: an ordering module imports the OpenWA adapter.
        (
            {
                "velora.ordering.service": {"velora.messaging.providers.openwa"},
                "velora.messaging.providers.openwa": set(),
            },
            "velora.ordering.service",
        ),
        # Indirect: through a helper module.
        (
            {
                "velora.menu.service": {"velora.messaging.helpers"},
                "velora.messaging.helpers": {"velora.messaging.providers"},
                "velora.messaging.providers": set(),
            },
            "velora.menu.service",
        ),
    ],
)
def test_the_check_catches_direct_and_indirect_paths(
    graph: dict[str, set[str]], offender: str
) -> None:
    assert offender in violations(graph)
