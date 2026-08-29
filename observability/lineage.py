"""Dataset and column lineage traversal utilities."""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any, Iterable


def _section(graph: dict[str, Any], name: str) -> dict[str, Any]:
    """Accept either a bare adjacency map or the JSON graph payload."""
    value = graph.get(name) if isinstance(graph, dict) else None
    return value if isinstance(value, dict) else graph


def _children(graph: dict[str, Any], node: str) -> Iterable[str]:
    values = graph.get(node, [])
    if values is None:
        return []
    if isinstance(values, str):
        return [values]
    return values


def _transitive_downstream(graph: dict[str, Any], start: str) -> list[str]:
    """Return a cycle-safe BFS traversal, excluding the starting node."""
    seen = {start}
    queue: deque[str] = deque([start])
    downstream: list[str] = []

    while queue:
        node = queue.popleft()
        for child in _children(graph, node):
            child = str(child)
            if child in seen:
                continue
            seen.add(child)
            downstream.append(child)
            queue.append(child)
    return downstream


def load_graph(path: str | Path) -> dict[str, list[str]]:
    """Load the dataset lineage section from the starter graph JSON."""
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return _section(payload, "dataset_lineage")


def get_downstream_assets(graph: dict[str, list[str]], start: str) -> list[str]:
    """Return all transitive downstream assets in deterministic BFS order."""
    return _transitive_downstream(_section(graph, "dataset_lineage"), start)


def get_column_downstream(
    column_graph: dict[str, list[str]], start_column: str
) -> list[str]:
    """Return all transitive downstream columns in deterministic BFS order."""
    return _transitive_downstream(_section(column_graph, "column_lineage"), start_column)


def _manifest_payload(manifest_path: str | Path) -> dict[str, Any]:
    path = Path(manifest_path)
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload if isinstance(payload, dict) else {}


def _node_records(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for section in ("nodes", "sources", "exposures", "metrics", "semantic_models"):
        values = manifest.get(section, {})
        if isinstance(values, dict):
            for unique_id, record in values.items():
                if isinstance(record, dict):
                    records[str(unique_id)] = record
    return records


def _append_unique(mapping: dict[str, list[str]], parent: str, child: str) -> None:
    children = mapping.setdefault(parent, [])
    if child not in children:
        children.append(child)


def extract_dbt_dataset_graph(manifest_path: str | Path) -> dict[str, list[str]]:
    """Extract a dependency graph from a dbt manifest.

    The returned keys are dbt unique_ids, matching the starter behavior. Newer
    manifests normally provide ``child_map``; for small hand-built manifests
    the function also derives the same graph from node ``depends_on`` entries
    or the inverse ``parent_map``.
    """
    manifest = _manifest_payload(manifest_path)
    if not manifest:
        return {}

    graph: dict[str, list[str]] = {}
    child_map = manifest.get("child_map")
    if isinstance(child_map, dict) and child_map:
        for parent, children in child_map.items():
            graph[str(parent)] = []
            for child in children if isinstance(children, (list, tuple, set)) else [children]:
                _append_unique(graph, str(parent), str(child))
        return graph

    records = _node_records(manifest)
    has_dependency_edges = False
    for unique_id, record in records.items():
        graph.setdefault(unique_id, [])
        dependencies = record.get("depends_on", {}).get("nodes", [])
        if isinstance(dependencies, (list, tuple, set)):
            for parent in dependencies:
                has_dependency_edges = True
                _append_unique(graph, str(parent), unique_id)

    if graph and (has_dependency_edges or not manifest.get("parent_map")):
        return graph

    parent_map = manifest.get("parent_map")
    if isinstance(parent_map, dict):
        for child, parents in parent_map.items():
            graph.setdefault(str(child), [])
            for parent in parents if isinstance(parents, (list, tuple, set)) else [parents]:
                _append_unique(graph, str(parent), str(child))
    return graph


def extract_dbt_asset_graph(
    manifest_path: str | Path,
    *,
    include_sources: bool = True,
    include_exposures: bool = True,
) -> dict[str, list[str]]:
    """Return a human-readable graph containing data assets, not test nodes.

    dbt's manifest child map includes data tests and unit tests. This helper
    filters those operational artifacts and maps unique_ids to model/seed
    names, which is more suitable for incident blast-radius reports.
    """
    manifest = _manifest_payload(manifest_path)
    graph = extract_dbt_dataset_graph(manifest_path)
    if not manifest or not graph:
        return {}

    records = _node_records(manifest)
    allowed_types = {"model", "seed", "snapshot"}
    if include_sources:
        allowed_types.add("source")
    if include_exposures:
        allowed_types.add("exposure")

    def is_allowed(unique_id: str) -> bool:
        record = records.get(unique_id)
        if record is None:
            # Preserve unknown IDs in hand-built manifests rather than
            # unexpectedly dropping information.
            return True
        return record.get("resource_type") in allowed_types

    def display_name(unique_id: str) -> str:
        record = records.get(unique_id, {})
        return str(record.get("alias") or record.get("name") or unique_id)

    named_graph: dict[str, list[str]] = {}
    for parent, children in graph.items():
        if not is_allowed(parent):
            continue
        named_parent = display_name(parent)
        named_graph.setdefault(named_parent, [])
        for child in children:
            if not is_allowed(child):
                continue
            named_child = display_name(child)
            _append_unique(named_graph, named_parent, named_child)
    return named_graph
