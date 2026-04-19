"""Sugiyama layered DAG layout for TouchDesigner subnets.

Pure stdlib. No TD imports — takes plain dicts, returns plain dicts.
Called by `td_api.py`'s POST /layout endpoint; also unit-tested in isolation.

Pipeline: cycle-break -> layer-assign -> crossing-reduce -> Brandes-Kopf coords.
"""
from __future__ import annotations

from typing import Any

DEFAULT_SPACING = {"rank": 150, "node": 30}
DEFAULT_RANK_GAP = 60   # pixels of empty space between rank columns (size-aware mode)
DEFAULT_NODE_GAP = 30   # pixels of empty space between stacked nodes (size-aware mode)
FALLBACK_NODE_W = 150   # assumed width for a node missing from `sizes`
FALLBACK_NODE_H = 100   # assumed height for a node missing from `sizes`
RELAX_ITERS = 32       # empirically sufficient for graphs <= ~50 nodes
PULL_FACTOR = 0.5      # under-relaxation factor; lower = slower but steadier
MAX_NODES = 500


def layout(graph: dict[str, Any]) -> dict[str, Any]:
    """Compute positions for a layered DAG.

    Input graph dict keys:
      - nodes: list of {"id": str, "is_feedback_top": bool?}
      - edges: list of {"from": id, "to": id}
      - direction: "LR" or "TB"  (default "LR")
      - sizes: dict[id, (width, height)] for node-size-aware placement (OPTIONAL).
          When provided, rank column widths and row heights come from the actual
          node dimensions plus `rank_gap`/`node_gap` padding. When omitted, falls
          back to fixed-step placement using `spacing["rank"]` and `spacing["node"]`.
      - spacing: {"rank": int, "node": int} fixed-step override (legacy; takes
          priority over size-aware mode when both `sizes` and explicit spacing
          are provided).
      - rank_gap: int, pixels of padding between ranks in size-aware mode.
      - node_gap: int, pixels of padding between rows in size-aware mode.

    Output dict keys:
      - positions: dict[id, (x, y)]
      - broken_edges: list of edges removed to break cycles
      - overlaps: list of {"a": id, "b": id, "rect_a": [l,b,r,t], "rect_b": ...}
          for any pair of nodes whose final bounding boxes intersect. Only
          populated when `sizes` is provided (size-aware mode); empty otherwise.
      - stats: counts (nodes, edges, layers, crossings_before, crossings_after)
    """
    nodes = graph.get("nodes", []) or []
    edges = graph.get("edges", []) or []
    explicit_spacing = graph.get("spacing") or {}
    spacing = {**DEFAULT_SPACING, **explicit_spacing}
    direction = graph.get("direction", "LR")
    sizes = graph.get("sizes") or {}
    rank_gap = int(graph.get("rank_gap", DEFAULT_RANK_GAP))
    node_gap = int(graph.get("node_gap", DEFAULT_NODE_GAP))

    if len(nodes) > MAX_NODES:
        return {"error": {"type": "TooLarge", "node_count": len(nodes),
                          "max": MAX_NODES}}

    if not nodes:
        return _result({}, [], 0, 0, 0, 0, overlaps=[])

    size_aware = bool(sizes) and not ("rank" in explicit_spacing or "node" in explicit_spacing)
    components = _split_components(nodes, edges)
    merged_positions: dict[str, tuple[int, int]] = {}
    merged_broken: list[dict] = []
    total_crossings_before = 0
    total_crossings_after = 0
    total_layers = 0
    y_offset = 0

    for comp_nodes, comp_edges in components:
        sub = _layout_single(
            comp_nodes, comp_edges, spacing, direction,
            sizes=sizes if size_aware else None,
            rank_gap=rank_gap, node_gap=node_gap,
        )
        if "error" in sub:
            return sub
        shifted = _shift_positions(sub["positions"], y_offset, direction)
        merged_positions.update(shifted)
        merged_broken.extend(sub["broken_edges"])
        total_crossings_before += sub["stats"]["crossings_before"]
        total_crossings_after += sub["stats"]["crossings_after"]
        total_layers = max(total_layers, sub["stats"]["layers"])
        # Advance the offset past the bottom of this component — use a stacking
        # step that accommodates size-aware mode (max node height in component).
        if shifted:
            axis = 1 if direction == "LR" else 0
            lowest = min(p[axis] for p in shifted.values())
            if size_aware:
                comp_max_h = max(
                    (sizes.get(n["id"], (FALLBACK_NODE_W, FALLBACK_NODE_H))[1]
                     for n in comp_nodes),
                    default=FALLBACK_NODE_H,
                )
                y_offset = lowest - (comp_max_h + 2 * node_gap)
            else:
                y_offset = lowest - 2 * spacing["node"]

    overlaps = _detect_overlaps(merged_positions, sizes) if size_aware else []

    return _result(merged_positions, merged_broken,
                   len(nodes), len(edges), total_layers,
                   total_crossings_before, total_crossings_after,
                   overlaps=overlaps)


def _layout_single(nodes, edges, spacing, direction,
                   sizes=None, rank_gap=DEFAULT_RANK_GAP, node_gap=DEFAULT_NODE_GAP):
    ids = [n["id"] for n in nodes]
    if len(ids) == 1:
        return _result({ids[0]: (0, 0)}, [], 1, 0, 1, 0)
    is_fb = {n["id"]: bool(n.get("is_feedback_top")) for n in nodes}
    dag_edges, broken = _break_cycles(ids, edges, is_fb)
    layers = _assign_layers(ids, dag_edges)
    layers, proper_edges = _insert_dummies(layers, dag_edges)
    c_before = _count_crossings(layers, proper_edges)
    layers = _reduce_crossings(layers, proper_edges)
    c_after = _count_crossings(layers, proper_edges)
    positions = _assign_coords(
        _strip_dummies(layers), proper_edges, spacing, direction,
        sizes=sizes, rank_gap=rank_gap, node_gap=node_gap,
    )
    return _result(positions, broken, len(nodes), len(edges), len(layers), c_before, c_after)


def _split_components(nodes, edges):
    """Weakly-connected components. Returns list of (node_list, edge_list) tuples."""
    parent: dict[str, str] = {n["id"]: n["id"] for n in nodes}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for e in edges:
        if e["from"] in parent and e["to"] in parent:
            union(e["from"], e["to"])

    buckets: dict[str, tuple[list, list]] = {}
    for n in nodes:
        buckets.setdefault(find(n["id"]), ([], []))[0].append(n)
    for e in edges:
        if e["from"] in parent:
            buckets[find(e["from"])][1].append(e)
    return list(buckets.values())


def _shift_positions(positions, offset, direction):
    out: dict[str, tuple[int, int]] = {}
    for nid, (x, y) in positions.items():
        if direction == "LR":
            out[nid] = (x, y + offset)
        else:
            out[nid] = (x + offset, y)
    return out


def _result(
    positions: dict[str, tuple[int, int]],
    broken_edges: list[dict],
    n_nodes: int,
    n_edges: int,
    n_layers: int,
    c_before: int,
    c_after: int | None = None,
    *,
    overlaps: list[dict] | None = None,
) -> dict[str, Any]:
    if c_after is None:
        c_after = c_before
    return {
        "positions": positions,
        "broken_edges": broken_edges,
        "overlaps": overlaps or [],
        "stats": {
            "nodes": n_nodes,
            "edges": n_edges,
            "layers": n_layers,
            "crossings_before": c_before,
            "crossings_after": c_after,
        },
    }


def _detect_overlaps(
    positions: dict[str, tuple[int, int]],
    sizes: dict[str, tuple[int, int]],
) -> list[dict]:
    """Report any pair of nodes whose bounding boxes intersect under the final plan.

    TD's `nodeX`, `nodeY` convention: (x, y) is the top-left of the node tile;
    width extends +x, height extends +y DOWN (which is -y in TD's visual coords,
    since layout returns y values where higher = higher on screen). Rect axes
    are [left, bottom, right, top] with bottom < top (we negate height).
    """
    rects: list[tuple[str, float, float, float, float]] = []
    for nid, (x, y) in positions.items():
        w, h = sizes.get(nid, (FALLBACK_NODE_W, FALLBACK_NODE_H))
        rects.append((nid, x, y - h, x + w, y))
    overlaps: list[dict] = []
    for i, (a, al, ab, ar, at) in enumerate(rects):
        for b, bl, bb, br, bt in rects[i + 1:]:
            if not (ar <= bl or br <= al or at <= bb or bt <= ab):
                overlaps.append({
                    "a": a, "b": b,
                    "rect_a": [al, ab, ar, at],
                    "rect_b": [bl, bb, br, bt],
                })
    return overlaps


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
    in_neighbours: dict[str, list[str]] = {}
    out_neighbours: dict[str, list[str]] = {}
    for row in layers:
        for n in row:
            in_neighbours.setdefault(n, [])
            out_neighbours.setdefault(n, [])
    for e in edges:
        out_neighbours.setdefault(e["from"], []).append(e["to"])
        in_neighbours.setdefault(e["to"], []).append(e["from"])

    best = [list(r) for r in layers]
    best_cost = _count_crossings(best, edges)
    current = [list(r) for r in layers]

    # snap_idx is the layer-position snapshot taken BEFORE each sort begins.
    # Never read current[i] from the key function — CPython's list.sort
    # temporarily swaps the list's internal buffer with an empty one during
    # sort (to detect mutation), so `current[i].index(nid)` inside a key
    # function raises ValueError for every nid. Snapshot instead.
    def barycenter(nid: str, i: int, use_in: bool, snap_idx: dict[str, int]) -> float:
        ref_layer = current[i - 1] if use_in else current[i + 1]
        order = {n: j for j, n in enumerate(ref_layer)}
        nbrs = in_neighbours[nid] if use_in else out_neighbours[nid]
        indices = [order[m] for m in nbrs if m in order]
        if indices:
            return sum(indices) / len(indices)
        return float(snap_idx.get(nid, 0))  # no neighbours -> pre-sort position

    for sweep in range(sweeps):
        if sweep % 2 == 0:
            # Down pass: each layer ordered by barycenter of predecessors.
            for i in range(1, len(current)):
                snap = {n: j for j, n in enumerate(current[i])}
                current[i].sort(key=lambda n, i=i, s=snap: barycenter(n, i, True, s))
        else:
            # Up pass: each layer ordered by barycenter of successors.
            for i in range(len(current) - 2, -1, -1):
                snap = {n: j for j, n in enumerate(current[i])}
                current[i].sort(key=lambda n, i=i, s=snap: barycenter(n, i, False, s))
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


def _assign_coords(
    layers: list[list[str]],
    edges: list[dict],
    spacing: dict[str, int],
    direction: str,
    *,
    sizes: dict[str, tuple[int, int]] | None = None,
    rank_gap: int = DEFAULT_RANK_GAP,
    node_gap: int = DEFAULT_NODE_GAP,
) -> dict[str, tuple[int, int]]:
    """Iterated-mean relaxation as a pragmatic stand-in for full Brandes-Köpf.

    Two placement modes:

    - **Fixed-step** (legacy, `sizes=None`): rank step = spacing["rank"], row
      step = spacing["node"]. Treats every node as a point and relies on the
      caller to pick spacings that accommodate their visual tiles.
    - **Size-aware** (`sizes` provided): each rank's X position is cumulative
      from the max node width in each preceding rank plus `rank_gap`. Row step
      is the max node height across all layers plus `node_gap`. Nodes larger
      than neighbours get room; nodes smaller stay tight.
    """
    order_of: dict[str, float] = {}
    for row in layers:
        for j, n in enumerate(row):
            order_of[n] = float(j)

    # Precompute neighbour lookups once (matches the pattern in _reduce_crossings).
    in_nbrs: dict[str, list[str]] = {n: [] for row in layers for n in row}
    out_nbrs: dict[str, list[str]] = {n: [] for row in layers for n in row}
    for e in edges:
        if e["from"] in in_nbrs and e["to"] in in_nbrs:
            in_nbrs[e["to"]].append(e["from"])
            out_nbrs[e["from"]].append(e["to"])

    # Relax: each non-terminal node's order-axis is the average of its neighbours'.
    # This is a simplification of full Brandes-Kopf that captures the essential
    # "center parent over children" property without the 200 LOC of block-alignment.
    for _ in range(RELAX_ITERS):
        new_order: dict[str, float] = dict(order_of)
        for row in layers:
            for n in row:
                up = [order_of[m] for m in in_nbrs[n]]
                down = [order_of[m] for m in out_nbrs[n]]
                neighbours = up + down
                if neighbours:
                    # Mean-pull; collapse is prevented by the min-gap pass below.
                    target = sum(neighbours) / len(neighbours)
                    new_order[n] = order_of[n] + PULL_FACTOR * (target - order_of[n])
        # Spread pass: preserve relative order within each layer, enforce min gap of 1.0.
        for row in layers:
            row_sorted = sorted(row, key=lambda n: new_order[n])
            for j, n in enumerate(row_sorted):
                if j == 0:
                    continue
                prev_order = new_order[row_sorted[j - 1]]
                if new_order[n] < prev_order + 1.0:
                    new_order[n] = prev_order + 1.0
        order_of = new_order

    # Build rank X offsets + uniform row step.
    positions: dict[str, tuple[int, int]] = {}
    if sizes:
        # Size-aware mode: x is cumulative by actual node widths; y step is
        # max node height across all layers so rows never overlap regardless
        # of which layer a tall node lives in.
        rank_widths = [
            max((sizes.get(n, (FALLBACK_NODE_W, FALLBACK_NODE_H))[0] for n in row),
                default=FALLBACK_NODE_W)
            for row in layers
        ]
        rank_x: list[int] = []
        cursor = 0
        for w in rank_widths:
            rank_x.append(cursor)
            cursor += w + rank_gap
        row_step = max(
            (sizes.get(n, (FALLBACK_NODE_W, FALLBACK_NODE_H))[1]
             for row in layers for n in row),
            default=FALLBACK_NODE_H,
        ) + node_gap
    else:
        rank_x = [rank * int(spacing["rank"]) for rank in range(len(layers))]
        row_step = int(spacing["node"])

    for rank, row in enumerate(layers):
        for n in row:
            x = rank_x[rank]
            y = int(order_of[n] * row_step)
            # Negate so lower order = higher on screen in TD's coord system.
            y = -y
            if direction == "TB":
                x, y = -y, x
            positions[n] = (x, y)
    return positions
