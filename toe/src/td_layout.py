"""Sugiyama layered DAG layout for TouchDesigner subnets.

Pure stdlib. No TD imports — takes plain dicts, returns plain dicts.
Called by `td_api.py`'s POST /layout endpoint; also unit-tested in isolation.

Pipeline: cycle-break -> layer-assign -> crossing-reduce -> Brandes-Kopf coords.
"""
from __future__ import annotations

from typing import Any

DEFAULT_SPACING = {"rank": 150, "node": 30}


def layout(graph: dict[str, Any]) -> dict[str, Any]:
    nodes = graph.get("nodes", []) or []
    edges = graph.get("edges", []) or []
    spacing = {**DEFAULT_SPACING, **(graph.get("spacing") or {})}
    direction = graph.get("direction", "LR")

    if not nodes:
        return _result(positions={}, broken_edges=[], n_nodes=0, n_edges=0, n_layers=0, crossings=0)
    if len(nodes) == 1:
        return _result(positions={nodes[0]["id"]: (0, 0)}, broken_edges=[], n_nodes=1, n_edges=0, n_layers=1, crossings=0)

    ids = [n["id"] for n in nodes]
    # Phase 1 (cycle break) filled in later; for now assume edges form a DAG.
    dag_edges = list(edges)
    broken: list[dict] = []

    layers = _assign_layers(ids, dag_edges)
    # Phases 3+4 are placeholders; use naive grid for now so tests can assert ordering.
    positions = _naive_coords(layers, spacing, direction)

    return _result(
        positions=positions,
        broken_edges=broken,
        n_nodes=len(nodes),
        n_edges=len(edges),
        n_layers=len(layers),
        crossings=0,
    )


def _result(
    positions: dict[str, tuple[int, int]],
    broken_edges: list[dict],
    n_nodes: int,
    n_edges: int,
    n_layers: int,
    crossings: int,
) -> dict[str, Any]:
    return {
        "positions": positions,
        "broken_edges": broken_edges,
        "stats": {
            "nodes": n_nodes,
            "edges": n_edges,
            "layers": n_layers,
            "crossings_before": crossings,
        },
    }


def _assign_layers(ids: list[str], edges: list[dict]) -> list[list[str]]:
    """Longest-path-from-source layer assignment. Returns list of ranks, each rank a list of ids."""
    preds: dict[str, list[str]] = {i: [] for i in ids}
    succs: dict[str, list[str]] = {i: [] for i in ids}
    for e in edges:
        preds[e["to"]].append(e["from"])
        succs[e["from"]].append(e["to"])

    layer: dict[str, int] = {}
    # Kahn-like topological walk.
    in_degree = {i: len(preds[i]) for i in ids}
    ready = [i for i in ids if in_degree[i] == 0]
    while ready:
        n = ready.pop(0)
        layer[n] = 0 if not preds[n] else 1 + max(layer[p] for p in preds[n])
        for s in succs[n]:
            in_degree[s] -= 1
            if in_degree[s] == 0:
                ready.append(s)

    if len(layer) != len(ids):
        raise ValueError("cycle detected; caller must break cycles before layer assignment")

    n_layers = max(layer.values()) + 1
    layers: list[list[str]] = [[] for _ in range(n_layers)]
    for i in ids:
        layers[layer[i]].append(i)
    return layers


def _naive_coords(
    layers: list[list[str]],
    spacing: dict[str, int],
    direction: str,
) -> dict[str, tuple[int, int]]:
    positions: dict[str, tuple[int, int]] = {}
    for rank, row in enumerate(layers):
        for order, node_id in enumerate(row):
            x = rank * spacing["rank"]
            y = -order * spacing["node"]  # negative so first is on top
            if direction == "TB":
                x, y = y, -x
            positions[node_id] = (x, y)
    return positions
