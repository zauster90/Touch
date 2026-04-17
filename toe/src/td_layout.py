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

    if not nodes:
        return _result(positions={}, broken_edges=[], n_nodes=0, n_edges=0, n_layers=0, crossings=0)
    if len(nodes) == 1:
        return _result(positions={nodes[0]["id"]: (0, 0)}, broken_edges=[], n_nodes=1, n_edges=0, n_layers=1, crossings=0)

    # Further phases added in later tasks.
    raise NotImplementedError("multi-node layout not yet implemented")


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
