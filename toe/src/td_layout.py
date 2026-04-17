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
        return _result({}, [], 0, 0, 0, 0)
    if len(nodes) == 1:
        return _result({nodes[0]["id"]: (0, 0)}, [], 1, 0, 1, 0)

    ids = [n["id"] for n in nodes]
    is_fb = {n["id"]: bool(n.get("is_feedback_top")) for n in nodes}

    dag_edges, broken = _break_cycles(ids, edges, is_fb)
    layers = _assign_layers(ids, dag_edges)
    layers, proper_edges = _insert_dummies(layers, dag_edges)
    crossings_before = _count_crossings(layers, proper_edges)
    layers = _reduce_crossings(layers, proper_edges)
    crossings_after = _count_crossings(layers, proper_edges)
    positions = _naive_coords(_strip_dummies(layers), spacing, direction)

    return _result(
        positions, broken, len(nodes), len(edges), len(layers),
        crossings_before, crossings_after,
    )


def _result(positions, broken_edges, n_nodes, n_edges, n_layers, c_before, c_after=None):
    if c_after is None:
        c_after = c_before
    return {
        "positions": positions,
        "broken_edges": broken_edges,
        "stats": {
            "nodes": n_nodes,
            "edges": n_edges,
            "layers": n_layers,
            "crossings_before": c_before,
            "crossings_after": c_after,
        },
    }


DUMMY_PREFIX = "__dummy__"


def _insert_dummies(layers, edges):
    """Subdivide every edge that spans > 1 layer with dummy nodes."""
    layer_of = {nid: i for i, row in enumerate(layers) for nid in row}
    new_layers = [list(row) for row in layers]
    new_edges: list[dict] = []
    dummy_counter = 0
    for e in edges:
        a, b = e["from"], e["to"]
        ra, rb = layer_of[a], layer_of[b]
        if rb - ra == 1:
            new_edges.append({"from": a, "to": b})
            continue
        prev = a
        for r in range(ra + 1, rb):
            d = f"{DUMMY_PREFIX}{dummy_counter}"
            dummy_counter += 1
            new_layers[r].append(d)
            new_edges.append({"from": prev, "to": d})
            prev = d
        new_edges.append({"from": prev, "to": b})
    return new_layers, new_edges


def _strip_dummies(layers):
    return [[n for n in row if not n.startswith(DUMMY_PREFIX)] for row in layers]


def _count_crossings(layers, edges):
    """Count edge crossings between adjacent layer pairs by position index."""
    total = 0
    for i in range(len(layers) - 1):
        top = {n: j for j, n in enumerate(layers[i])}
        bot = {n: j for j, n in enumerate(layers[i + 1])}
        es = [(top[e["from"]], bot[e["to"]])
              for e in edges if e["from"] in top and e["to"] in bot]
        for a in range(len(es)):
            for b in range(a + 1, len(es)):
                (u1, v1), (u2, v2) = es[a], es[b]
                if (u1 < u2 and v1 > v2) or (u1 > u2 and v1 < v2):
                    total += 1
    return total


def _reduce_crossings(layers, edges, sweeps: int = 24):
    """Barycentric sweep: 12 down-passes + 12 up-passes, keep the best."""
    best = [list(r) for r in layers]
    best_cost = _count_crossings(best, edges)
    current = [list(r) for r in layers]

    def barycenter(nid, neighbour_layer_idx, current_layers):
        order = {n: j for j, n in enumerate(current_layers[neighbour_layer_idx])}
        neighbours = [order[e["from"]] for e in edges
                      if e["to"] == nid and e["from"] in order]
        neighbours += [order[e["to"]] for e in edges
                       if e["from"] == nid and e["to"] in order]
        return sum(neighbours) / len(neighbours) if neighbours else 0.0

    for sweep in range(sweeps):
        if sweep % 2 == 0:
            # Down pass: each layer ordered by barycenter of previous layer.
            for i in range(1, len(current)):
                current[i].sort(key=lambda n: barycenter(n, i - 1, current))
        else:
            # Up pass.
            for i in range(len(current) - 2, -1, -1):
                current[i].sort(key=lambda n: barycenter(n, i + 1, current))
        cost = _count_crossings(current, edges)
        if cost < best_cost:
            best = [list(r) for r in current]
            best_cost = cost
    return best


def _break_cycles(
    ids: list[str],
    edges: list[dict],
    is_feedback_top: dict[str, bool],
) -> tuple[list[dict], list[dict]]:
    """Greedy feedback-arc set with a bias for edges touching feedbackTOP nodes."""
    current: list[dict] = list(edges)
    broken: list[dict] = []

    def has_cycle(es: list[dict]) -> bool:
        succs: dict[str, list[str]] = {i: [] for i in ids}
        for e in es:
            succs[e["from"]].append(e["to"])
        in_deg: dict[str, int] = {i: 0 for i in ids}
        for e in es:
            in_deg[e["to"]] += 1
        ready = [i for i in ids if in_deg[i] == 0]
        seen = 0
        while ready:
            n = ready.pop()
            seen += 1
            for s in succs[n]:
                in_deg[s] -= 1
                if in_deg[s] == 0:
                    ready.append(s)
        return seen != len(ids)

    def edge_score(e: dict) -> int:
        out_deg = sum(1 for x in current if x["from"] == e["from"])
        in_deg = sum(1 for x in current if x["to"] == e["to"])
        # Prefer edges whose source is a feedbackTOP: feedback nodes emit back-edges.
        fb_bias = -1 if is_feedback_top.get(e["from"]) else 0
        return out_deg - in_deg + fb_bias

    while has_cycle(current):
        feedback_candidates = [
            e for e in current
            if is_feedback_top.get(e["from"]) or is_feedback_top.get(e["to"])
        ]
        pool = feedback_candidates or current
        pick = min(pool, key=edge_score)
        current.remove(pick)
        broken.append({
            "from": pick["from"],
            "to": pick["to"],
            "on_feedback_top": is_feedback_top.get(pick["from"], False)
                               or is_feedback_top.get(pick["to"], False),
        })

    return current, broken


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
