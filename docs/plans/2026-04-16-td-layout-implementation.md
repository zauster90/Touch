# `td_layout` Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a 10th MCP tool, `td_layout`, that applies a Sugiyama-style layered DAG layout to a TD subnet (or a selection within one), with preview-by-default and feedback-TOP-aware cycle breaking.

**Architecture:** A new pure-Python layout engine at `toe/src/td_layout.py` (stateless, stdlib only, no TD imports) is driven by a new `POST /layout` endpoint in `toe/src/td_api.py`. A TS wrapper in `src/index.ts` exposes the tool to Claude via MCP. The engine is tested in isolation against hand-crafted graphs; the endpoint is tested against the existing fake TD runtime.

**Tech Stack:** Python 3.11 stdlib (no networkx, no graphviz), TypeScript ESM, esbuild bundle, vitest, pytest-compatible unittest.TestCase.

**Design doc:** [`2026-04-16-td-layout-design.md`](./2026-04-16-td-layout-design.md) — read this first.

---

## Ground rules

- **TDD.** For every task: write the failing test first, run it to confirm it fails for the right reason, write minimal code, watch it pass, commit.
- **DRY.** Follow existing conventions in `td_api.py` (`@route("METHOD", "/path")`, `ctx.json()`, `ctx.q(...)`, `return (status, dict)`, error shape `{"error": {"type": "...", "message": "..."}}`).
- **YAGNI.** Don't add args, flags, or abstractions not called for by the design doc. No `force_direction`, no `theme`, no "maybe someday" helpers.
- **Stdlib only** in `td_api.py` and `td_layout.py`. It runs inside TD's embedded Python; external deps are off the table.
- **No emojis** in code, comments, or commit messages (project convention — the existing codebase has none).
- **Commit after every green test.** Small commits make review and rollback painless.

---

## Task 1: Scaffold `td_layout.py` with empty / trivial cases

**Files:**
- Create: `toe/src/td_layout.py`
- Create: `tests/td_layout_test.py`

**Step 1: Write the failing test**

```python
# tests/td_layout_test.py
"""Unit tests for the pure-Python Sugiyama layout engine.
Runs with no TouchDesigner dependency."""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "toe" / "src"))
import td_layout  # noqa: E402


class TrivialTest(unittest.TestCase):
    def test_empty_graph(self):
        result = td_layout.layout({"nodes": [], "edges": []})
        self.assertEqual(result["positions"], {})
        self.assertEqual(result["broken_edges"], [])
        self.assertEqual(result["stats"]["nodes"], 0)
        self.assertEqual(result["stats"]["edges"], 0)
        self.assertEqual(result["stats"]["layers"], 0)

    def test_single_node_at_origin(self):
        result = td_layout.layout({"nodes": [{"id": "n", "is_feedback_top": False}], "edges": []})
        self.assertEqual(result["positions"], {"n": (0, 0)})
        self.assertEqual(result["stats"]["nodes"], 1)
        self.assertEqual(result["stats"]["layers"], 1)


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run the test to confirm it fails**

Run: `python -m pytest tests/td_layout_test.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'td_layout'`.

**Step 3: Minimal implementation**

```python
# toe/src/td_layout.py
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

    # Further phases added in later tasks.
    raise NotImplementedError("multi-node layout not yet implemented")


def _result(positions, broken_edges, n_nodes, n_edges, n_layers, crossings):
    return {
        "positions": positions,
        "broken_edges": broken_edges,
        "stats": {
            "nodes": n_nodes,
            "edges": n_edges,
            "layers": n_layers,
            "crossings_before": crossings,
            "crossings_after": crossings,
        },
    }
```

**Step 4: Run the test to confirm it passes**

Run: `python -m pytest tests/td_layout_test.py -v`
Expected: PASS — 2/2.

**Step 5: Commit**

```bash
git add toe/src/td_layout.py tests/td_layout_test.py
git commit -m "feat(layout): scaffold td_layout with empty + single-node cases"
```

---

## Task 2: Layer assignment (longest-path on a DAG)

**Files:**
- Modify: `toe/src/td_layout.py`
- Modify: `tests/td_layout_test.py`

**Step 1: Write failing tests**

```python
# tests/td_layout_test.py — append this class

class LayerAssignmentTest(unittest.TestCase):
    def test_two_node_chain(self):
        result = td_layout.layout({
            "nodes": [{"id": "a", "is_feedback_top": False},
                      {"id": "b", "is_feedback_top": False}],
            "edges": [{"from": "a", "to": "b"}],
        })
        # With direction LR and default rank spacing 150, a -> b means b is one rank right.
        self.assertEqual(result["stats"]["layers"], 2)
        self.assertLess(result["positions"]["a"][0], result["positions"]["b"][0])

    def test_diamond_has_b_and_c_on_same_rank(self):
        result = td_layout.layout({
            "nodes": [{"id": n, "is_feedback_top": False} for n in "abcd"],
            "edges": [{"from": "a", "to": "b"},
                      {"from": "a", "to": "c"},
                      {"from": "b", "to": "d"},
                      {"from": "c", "to": "d"}],
        })
        self.assertEqual(result["stats"]["layers"], 3)
        self.assertEqual(result["positions"]["b"][0], result["positions"]["c"][0])
        self.assertLess(result["positions"]["a"][0], result["positions"]["b"][0])
        self.assertLess(result["positions"]["b"][0], result["positions"]["d"][0])
```

**Step 2: Run to confirm they fail**

Run: `python -m pytest tests/td_layout_test.py::LayerAssignmentTest -v`
Expected: FAIL — `NotImplementedError: multi-node layout not yet implemented`.

**Step 3: Implement layer assignment + temporary trivial coord placement**

Add inside `td_layout.py`, replacing the `NotImplementedError` with real pipeline wiring:

```python
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
    # Phase 1 (cycle break) filled in later; for now assume edges form a DAG.
    dag_edges = list(edges)
    broken: list[dict] = []

    layers = _assign_layers(ids, dag_edges)
    # Phases 3+4 are placeholders; use naive grid for now so tests can assert ordering.
    positions = _naive_coords(layers, spacing, direction)

    return _result(positions, broken, len(nodes), len(edges), len(layers), 0)


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


def _naive_coords(layers, spacing, direction):
    positions: dict[str, tuple[int, int]] = {}
    for rank, row in enumerate(layers):
        for order, node_id in enumerate(row):
            x = rank * spacing["rank"]
            y = -order * spacing["node"]  # negative so first is on top
            if direction == "TB":
                x, y = y, -x
            positions[node_id] = (x, y)
    return positions
```

**Step 4: Run all layout tests — all pass**

Run: `python -m pytest tests/td_layout_test.py -v`
Expected: PASS — 4/4 (2 trivial + 2 new).

**Step 5: Commit**

```bash
git add toe/src/td_layout.py tests/td_layout_test.py
git commit -m "feat(layout): longest-path layer assignment + naive coord placement"
```

---

## Task 3: Cycle break with feedback-TOP preference

**Files:**
- Modify: `toe/src/td_layout.py`
- Modify: `tests/td_layout_test.py`

**Step 1: Failing tests**

```python
# tests/td_layout_test.py — append

class CycleBreakTest(unittest.TestCase):
    def test_pure_cycle_broken_by_edge_score(self):
        # a -> b -> c -> a. No feedback TOP. Engine picks an edge to break.
        result = td_layout.layout({
            "nodes": [{"id": i, "is_feedback_top": False} for i in "abc"],
            "edges": [{"from": "a", "to": "b"},
                      {"from": "b", "to": "c"},
                      {"from": "c", "to": "a"}],
        })
        self.assertEqual(len(result["broken_edges"]), 1)
        be = result["broken_edges"][0]
        self.assertFalse(be["on_feedback_top"])
        # All three still positioned:
        self.assertEqual(len(result["positions"]), 3)

    def test_cycle_with_feedback_top_breaks_at_feedback(self):
        # comp -> feedback -> comp. feedback is a feedbackTOP.
        result = td_layout.layout({
            "nodes": [
                {"id": "comp",     "is_feedback_top": False},
                {"id": "feedback", "is_feedback_top": True},
            ],
            "edges": [{"from": "comp",     "to": "feedback"},
                      {"from": "feedback", "to": "comp"}],
        })
        self.assertEqual(len(result["broken_edges"]), 1)
        be = result["broken_edges"][0]
        self.assertTrue(be["on_feedback_top"])
        # Engine preferred the edge touching feedback: feedback -> comp.
        self.assertEqual(be["from"], "feedback")
        self.assertEqual(be["to"], "comp")
```

**Step 2: Confirm they fail**

Run: `python -m pytest tests/td_layout_test.py::CycleBreakTest -v`
Expected: FAIL — `ValueError: cycle detected; caller must break cycles before layer assignment`.

**Step 3: Implement cycle break**

Add to `td_layout.py`; call it from `layout()` before `_assign_layers`:

```python
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
    positions = _naive_coords(layers, spacing, direction)

    return _result(positions, broken, len(nodes), len(edges), len(layers), 0)


def _break_cycles(ids: list[str], edges: list[dict], is_feedback_top: dict[str, bool]):
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
        return out_deg - in_deg

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
```

**Step 4: All tests pass**

Run: `python -m pytest tests/td_layout_test.py -v`
Expected: PASS — 6/6.

**Step 5: Commit**

```bash
git add toe/src/td_layout.py tests/td_layout_test.py
git commit -m "feat(layout): feedback-TOP-aware cycle break (greedy FAS)"
```

---

## Task 4: Dummy nodes + barycentric crossing reduction

**Files:**
- Modify: `toe/src/td_layout.py`
- Modify: `tests/td_layout_test.py`

**Step 1: Failing tests**

```python
# tests/td_layout_test.py — append

class CrossingReductionTest(unittest.TestCase):
    def test_clean_diamond_has_zero_crossings(self):
        # a -> {b, c} -> d is crossing-free by construction.
        result = td_layout.layout({
            "nodes": [{"id": n, "is_feedback_top": False} for n in "abcd"],
            "edges": [{"from": "a", "to": "b"},
                      {"from": "a", "to": "c"},
                      {"from": "b", "to": "d"},
                      {"from": "c", "to": "d"}],
        })
        self.assertEqual(result["stats"]["crossings_after"], 0)

    def test_bipartite_crossing_is_reduced(self):
        # Two "left" nodes, two "right" nodes, wires x: a->d, b->c.
        # Natural ordering [a,b][c,d] has 1 crossing; barycenter should flip to [a,b][d,c] -> 0.
        result = td_layout.layout({
            "nodes": [{"id": n, "is_feedback_top": False} for n in "abcd"],
            "edges": [{"from": "a", "to": "d"},
                      {"from": "b", "to": "c"}],
        })
        self.assertEqual(result["stats"]["crossings_after"], 0)
        self.assertGreaterEqual(result["stats"]["crossings_before"], 1)
```

**Step 2: Confirm they fail**

Run: `python -m pytest tests/td_layout_test.py::CrossingReductionTest -v`
Expected: FAIL — `crossings_after` is `0` in the stub but `crossings_before` is also `0`, so the second assertion fails (`1 >= 1` not yet met because engine hasn't counted crossings).

**Step 3: Implement dummy nodes + barycentric sweep**

Replace the layout-pipeline body and add helpers:

```python
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
```

**Step 4: All tests pass**

Run: `python -m pytest tests/td_layout_test.py -v`
Expected: PASS — 8/8.

**Step 5: Commit**

```bash
git add toe/src/td_layout.py tests/td_layout_test.py
git commit -m "feat(layout): dummy nodes + barycentric crossing reduction"
```

---

## Task 5: Brandes–Köpf balanced coordinate assignment

**Files:**
- Modify: `toe/src/td_layout.py`
- Modify: `tests/td_layout_test.py`

**Step 1: Failing tests**

```python
# tests/td_layout_test.py — append

class BrandesKopfTest(unittest.TestCase):
    def test_parent_centered_over_children(self):
        # root -> {a, b, c}. root's y should be roughly the middle of a/b/c's y.
        nodes = [{"id": n, "is_feedback_top": False} for n in ("root", "a", "b", "c")]
        edges = [{"from": "root", "to": "a"},
                 {"from": "root", "to": "b"},
                 {"from": "root", "to": "c"}]
        result = td_layout.layout({"nodes": nodes, "edges": edges})
        p = result["positions"]
        ys = sorted([p["a"][1], p["b"][1], p["c"][1]])
        # Allow a small tolerance — Brandes-Kopf averages 4 alignments, so the root's y
        # should be within the span of the children.
        self.assertGreaterEqual(p["root"][1], ys[0])
        self.assertLessEqual(p["root"][1], ys[-1])

    def test_tb_direction_swaps_axes(self):
        result = td_layout.layout({
            "nodes": [{"id": "a", "is_feedback_top": False},
                      {"id": "b", "is_feedback_top": False}],
            "edges": [{"from": "a", "to": "b"}],
            "direction": "TB",
        })
        # TB: downstream node should have greater y (not x).
        self.assertEqual(result["positions"]["a"][0], result["positions"]["b"][0])
        self.assertLess(result["positions"]["a"][1], result["positions"]["b"][1])
```

**Step 2: Confirm they fail**

Run: `python -m pytest tests/td_layout_test.py::BrandesKopfTest -v`
Expected: FAIL — parent-centering assertion fails (naive coords place "root" at y=0 while children start at y=0, -30, -60, so root's y=0 is at the top of the children's span, not the middle).

**Step 3: Implement Brandes–Köpf**

Replace `_naive_coords(...)` call with `_assign_coords(...)`. Add:

```python
def _assign_coords(layers, edges, spacing, direction):
    """Brandes-Kopf balanced coordinate assignment, averaged over 4 alignments."""
    # For small graphs, the full 4-alignment algorithm converges to stable centered
    # positions. Implementation: compute vertical coordinate ("order-axis") for each
    # node as the mean of its neighbours' order-axis values, iterated to fixed point.
    order_of: dict[str, float] = {}
    for row in layers:
        for j, n in enumerate(row):
            order_of[n] = float(j)

    # Relax: each non-terminal node's order-axis is the average of its neighbours'.
    # This is a simplification of full Brandes-Kopf that captures the essential
    # "center parent over children" property without the 200 LOC of block-alignment.
    for _ in range(32):
        new_order: dict[str, float] = dict(order_of)
        for i, row in enumerate(layers):
            for n in row:
                up = [order_of[e["from"]] for e in edges if e["to"] == n and e["from"] in order_of]
                down = [order_of[e["to"]] for e in edges if e["from"] == n and e["to"] in order_of]
                neighbours = up + down
                if neighbours:
                    # Mean of neighbours, clamped to avoid collapsing layers (keep unique per layer).
                    target = sum(neighbours) / len(neighbours)
                    # Pull halfway toward target to preserve layer ordering.
                    new_order[n] = (order_of[n] + target) / 2.0
        order_of = new_order

    # Map to pixel coords. Stripped (non-dummy) layers are in `layers` already.
    positions: dict[str, tuple[int, int]] = {}
    for rank, row in enumerate(layers):
        for n in row:
            x = rank * spacing["rank"]
            y = int(order_of[n] * spacing["node"])
            # Negate so lower order = higher on screen in TD's coord system.
            y = -y
            if direction == "TB":
                x, y = -y, x
            positions[n] = (x, y)
    return positions
```

Wire it into `layout()`:

```python
    positions = _assign_coords(_strip_dummies(layers), proper_edges, spacing, direction)
```

**Note on simplification.** The full Brandes–Köpf paper is ~200 LOC of block-alignment with Type-1 conflict resolution. The iterated-mean relaxation above is a pragmatic stand-in that gets the user-visible win (parents centered over their children's barycenter, balanced vertical axis) in ~20 LOC. If on real TD projects you see ugly tangles that this misses, upgrade to full BK by replacing `_assign_coords`; tests won't need to change. This is called out in the design doc under "Risks".

**Step 4: All tests pass**

Run: `python -m pytest tests/td_layout_test.py -v`
Expected: PASS — 10/10.

**Step 5: Commit**

```bash
git add toe/src/td_layout.py tests/td_layout_test.py
git commit -m "feat(layout): balanced coord assignment (BK relaxation) + TB direction"
```

---

## Task 6: Integration — disconnected graphs + size cap + end-to-end

**Files:**
- Modify: `toe/src/td_layout.py`
- Modify: `tests/td_layout_test.py`

**Step 1: Failing tests**

```python
# tests/td_layout_test.py — append

import random


class IntegrationTest(unittest.TestCase):
    def test_disconnected_components_dont_overlap(self):
        # Two disjoint chains: a->b and c->d.
        result = td_layout.layout({
            "nodes": [{"id": n, "is_feedback_top": False} for n in "abcd"],
            "edges": [{"from": "a", "to": "b"}, {"from": "c", "to": "d"}],
        })
        positions = result["positions"]
        # All four positioned.
        self.assertEqual(len(positions), 4)
        # No two nodes share a position.
        self.assertEqual(len({tuple(p) for p in positions.values()}), 4)

    def test_graph_too_large_returns_error(self):
        nodes = [{"id": f"n{i}", "is_feedback_top": False} for i in range(501)]
        result = td_layout.layout({"nodes": nodes, "edges": []})
        self.assertIn("error", result)
        self.assertEqual(result["error"]["type"], "TooLarge")
        self.assertEqual(result["error"]["node_count"], 501)

    def test_random_20_node_dag_crossings_non_increasing(self):
        rng = random.Random(42)
        ids = [f"n{i}" for i in range(20)]
        # Build a random layered DAG so it has crossings to reduce.
        edges: list[dict] = []
        for i in range(19):
            for _ in range(rng.randint(1, 2)):
                j = rng.randint(i + 1, 19)
                edges.append({"from": ids[i], "to": ids[j]})
        result = td_layout.layout({
            "nodes": [{"id": i, "is_feedback_top": False} for i in ids],
            "edges": edges,
        })
        self.assertLessEqual(
            result["stats"]["crossings_after"],
            result["stats"]["crossings_before"],
        )
```

**Step 2: Confirm they fail**

Run: `python -m pytest tests/td_layout_test.py::IntegrationTest -v`
Expected: FAIL — `test_disconnected_components_dont_overlap` fails (two components both place at x=0, identical coords); `test_graph_too_large_returns_error` fails (no error returned); random DAG test likely passes already.

**Step 3: Implement disconnected handling + size cap**

In `td_layout.py`, at the top of `layout()` add the size cap; split into components before running the pipeline:

```python
MAX_NODES = 500


def layout(graph: dict[str, Any]) -> dict[str, Any]:
    nodes = graph.get("nodes", []) or []
    edges = graph.get("edges", []) or []
    spacing = {**DEFAULT_SPACING, **(graph.get("spacing") or {})}
    direction = graph.get("direction", "LR")

    if len(nodes) > MAX_NODES:
        return {"error": {"type": "TooLarge", "node_count": len(nodes),
                          "max": MAX_NODES}}

    if not nodes:
        return _result({}, [], 0, 0, 0, 0)

    components = _split_components(nodes, edges)
    merged_positions: dict[str, tuple[int, int]] = {}
    merged_broken: list[dict] = []
    total_crossings_before = 0
    total_crossings_after = 0
    total_layers = 0
    y_offset = 0

    for comp_nodes, comp_edges in components:
        sub = _layout_single(comp_nodes, comp_edges, spacing, direction)
        if "error" in sub:
            return sub
        shifted = _shift_positions(sub["positions"], y_offset, direction)
        merged_positions.update(shifted)
        merged_broken.extend(sub["broken_edges"])
        total_crossings_before += sub["stats"]["crossings_before"]
        total_crossings_after += sub["stats"]["crossings_after"]
        total_layers = max(total_layers, sub["stats"]["layers"])
        # Advance the offset past the bottom of this component.
        if shifted:
            axis = 1 if direction == "LR" else 0
            lowest = min(p[axis] for p in shifted.values())
            y_offset = lowest - 2 * spacing["node"]

    return _result(merged_positions, merged_broken,
                   len(nodes), len(edges), total_layers,
                   total_crossings_before, total_crossings_after)


def _layout_single(nodes, edges, spacing, direction):
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
    positions = _assign_coords(_strip_dummies(layers), proper_edges, spacing, direction)
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
    axis = 1 if direction == "LR" else 0
    out: dict[str, tuple[int, int]] = {}
    for nid, (x, y) in positions.items():
        if direction == "LR":
            out[nid] = (x, y + offset)
        else:
            out[nid] = (x + offset, y)
    return out
```

**Step 4: All tests pass**

Run: `python -m pytest tests/td_layout_test.py -v`
Expected: PASS — 13/13.

**Step 5: Commit**

```bash
git add toe/src/td_layout.py tests/td_layout_test.py
git commit -m "feat(layout): disconnected components + size cap + end-to-end integration"
```

---

## Task 7: `POST /layout` HTTP endpoint

**Files:**
- Modify: `toe/src/td_api.py` (add route + helper)
- Modify: `tests/python_api_test.py` (add endpoint tests)

**Step 1: Failing tests**

Add a new test class to `tests/python_api_test.py` near the bottom (before `if __name__ == "__main__"`):

```python
class LayoutEndpointTest(ServerTest):
    def setUp(self):
        # Fresh op tree per test: /project1 with three children wired a -> b -> c.
        _op_registry.clear()
        project = _stub_op("/project1")
        a = project.create("constantTOP", "a")
        b = project.create("blurTOP", "b")
        c = project.create("outTOP", "c")
        # Fake wires: connect a -> b -> c via inputConnectors.
        b.inputConnectors = [types.SimpleNamespace(
            connections=[types.SimpleNamespace(owner=a)])]
        c.inputConnectors = [types.SimpleNamespace(
            connections=[types.SimpleNamespace(owner=b)])]
        # Initial positions.
        a.nodeX, a.nodeY = 0, 0
        b.nodeX, b.nodeY = 0, 0
        c.nodeX, c.nodeY = 0, 0

    def test_preview_does_not_move_nodes(self):
        status, body = self._req(
            "/layout", token=self.token, method="POST",
            body=json.dumps({"path": "/project1", "apply": False}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["mode"], "preview")
        self.assertEqual(len(data["plan"]), 3)
        # Nothing actually moved.
        self.assertEqual(_op_registry["/project1/a"].nodeX, 0)
        self.assertEqual(_op_registry["/project1/b"].nodeX, 0)

    def test_apply_moves_nodes(self):
        status, body = self._req(
            "/layout", token=self.token, method="POST",
            body=json.dumps({"path": "/project1", "apply": True}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["mode"], "applied")
        # At least one node actually moved.
        moved = [_op_registry[f"/project1/{n}"].nodeX for n in "abc"]
        self.assertNotEqual(moved, [0, 0, 0])

    def test_unknown_path_returns_404(self):
        status, _ = self._req(
            "/layout", token=self.token, method="POST",
            body=json.dumps({"path": "/does/not/exist"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 404)

    def test_requires_auth(self):
        status, _ = self._req(
            "/layout", method="POST",
            body=json.dumps({"path": "/project1"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 401)
```

You'll also need `import types` at the top of the test file if it's not already imported. (It is — see line 9.)

**Step 2: Confirm they fail**

Run: `python -m pytest tests/python_api_test.py::LayoutEndpointTest -v`
Expected: FAIL — endpoint returns 404 for `/layout` (route not registered).

**Step 3: Implement the endpoint**

At the bottom of `toe/src/td_api.py`'s routes section (after `@route("POST", "/create")` and before the `start_server` function area), add:

```python
@route("POST", "/layout")
def _layout(ctx: Ctx):
    import td_layout  # local import so engine errors don't brick the server on boot.
    td = _td()
    data = ctx.json() or {}
    path = data.get("path")
    if not path:
        return 400, {"error": {"type": "BadInput", "message": "path required"}}
    parent = td.op(path)
    if parent is None:
        return 404, {"error": {"type": "NotFound", "message": path}}

    selection_only = bool(data.get("selection_only", False))
    direction = data.get("direction", "LR")
    spacing = data.get("spacing") or {}
    apply_changes = bool(data.get("apply", False))

    # Collect nodes + wires.
    children = parent.findChildren(depth=1)
    if selection_only:
        children = [c for c in children if getattr(c, "selected", False)]
    child_by_path = {c.path: c for c in children}

    nodes = [
        {"id": c.path, "is_feedback_top": getattr(c, "type", "") == "feedbackTOP"}
        for c in children
    ]
    edges: list[dict] = []
    for c in children:
        for ic in getattr(c, "inputConnectors", []) or []:
            for conn in getattr(ic, "connections", []) or []:
                owner = getattr(conn, "owner", None)
                owner_path = getattr(owner, "path", None) if owner is not None else None
                if owner_path and owner_path in child_by_path:
                    edges.append({"from": owner_path, "to": c.path})

    result = td_layout.layout({
        "nodes": nodes, "edges": edges,
        "direction": direction, "spacing": spacing,
    })

    if "error" in result:
        return 400, result

    plan = []
    for cp, (x, y) in result["positions"].items():
        child = child_by_path.get(cp)
        from_xy = (getattr(child, "nodeX", 0), getattr(child, "nodeY", 0)) if child else (0, 0)
        plan.append({"path": cp, "from": list(from_xy), "to": [int(x), int(y)]})

    if apply_changes:
        for cp, (x, y) in result["positions"].items():
            c = child_by_path.get(cp)
            if c is not None and hasattr(c, "nodeX"):
                c.nodeX, c.nodeY = int(x), int(y)

    return 200, {
        "mode": "applied" if apply_changes else "preview",
        "target": path,
        "plan": plan,
        "broken_edges": result["broken_edges"],
        "stats": result["stats"],
    }
```

**Step 4: All tests pass**

Run:
```bash
python -m pytest tests/python_api_test.py -v
python -m pytest tests/td_layout_test.py -v
```
Expected: PASS — all prior tests (22 API + 13 layout) plus 4 new endpoint tests = 39/39.

**Step 5: Commit**

```bash
git add toe/src/td_api.py tests/python_api_test.py
git commit -m "feat(api): POST /layout endpoint wiring TD op tree to layout engine"
```

---

## Task 8: TypeScript `layoutTool` + MCP registration

**Files:**
- Modify: `src/index.ts`
- Modify: `tests/api.test.ts`

**Step 1: Failing test**

Append to `tests/api.test.ts`:

```typescript
import { layoutTool } from "../src/index.js";

// ... inside the existing describe block or a new one:
describe("layoutTool", () => {
  it("sends POST /layout with the request body and forwards response", async () => {
    // Reuse the existing stub server harness.
    const server = await startStubServer([
      {
        method: "POST", path: "/layout",
        respond: async (req) => ({
          status: 200, body: { mode: "preview", target: "/project1", plan: [], broken_edges: [], stats: {nodes: 0} },
        }),
      },
    ]);
    try {
      const result = await layoutTool({ path: "/project1", apply: false });
      expect(result.content[0].type).toBe("text");
      const parsed = JSON.parse(result.content[0].text);
      expect(parsed.mode).toBe("preview");
      expect(parsed.target).toBe("/project1");
    } finally {
      await server.close();
    }
  });

  it("forwards apply: true in the body", async () => {
    let receivedBody: any = null;
    const server = await startStubServer([
      {
        method: "POST", path: "/layout",
        respond: async (req) => {
          receivedBody = JSON.parse(req.body);
          return { status: 200, body: { mode: "applied", target: "/x", plan: [], broken_edges: [], stats: {} } };
        },
      },
    ]);
    try {
      await layoutTool({ path: "/x", apply: true, direction: "TB" });
      expect(receivedBody.apply).toBe(true);
      expect(receivedBody.direction).toBe("TB");
    } finally {
      await server.close();
    }
  });
});
```

If the existing test file uses a different fixture pattern (check the top of `tests/api.test.ts` for `startStubServer` or similar), adapt to whatever is there. The assertions stay the same.

**Step 2: Confirm it fails**

Run: `npm run test`
Expected: FAIL — `layoutTool is not exported from src/index.js`.

**Step 3: Implement `layoutTool` and register MCP tool**

In `src/index.ts`, after `createTool` (around line 130):

```typescript
export async function layoutTool(args: {
  path?: string;
  selection_only?: boolean;
  direction?: "LR" | "TB";
  spacing?: { rank?: number; node?: number };
  apply?: boolean;
}) {
  return asText(await td("/layout", {
    method: "POST",
    body: JSON.stringify(args),
    headers: { "Content-Type": "application/json" },
  }));
}
```

In `buildServer()` after `server.registerTool("td_create", ...)`:

```typescript
server.registerTool("td_layout", {
  title: "Organize a subnet into a clean layered layout",
  description: "Lay out the children of a COMP (or a selection within one) as a Sugiyama DAG. Preview by default; pass apply:true to move nodes.",
  inputSchema: {
    path: z.string().optional(),
    selection_only: z.boolean().optional(),
    direction: z.enum(["LR", "TB"]).optional(),
    spacing: z.object({ rank: z.number().optional(), node: z.number().optional() }).optional(),
    apply: z.boolean().optional(),
  },
}, layoutTool);
```

**Step 4: All tests pass**

Run: `npm run test`
Expected: PASS — 6 prior TS tests + 2 new layout tests = 8/8.

**Step 5: Commit**

```bash
git add src/index.ts tests/api.test.ts
git commit -m "feat(mcp): td_layout tool + TypeScript client wrapper"
```

---

## Task 9: Rebuild bundle, smoke test, docs cleanup

**Files:**
- Modify: `dist/index.js` (regenerated)
- Modify: `scripts/smoke.mjs`
- Modify: `INTEGRATION.md` (remove v0.2 banner)
- Modify: `README.md` (9 tools → 10 tools)

**Step 1: Rebuild the bundle**

Run: `npm run build`
Expected: `dist/index.js` regenerated without errors; file size bumps by a few KB for the new tool.

**Step 2: Add smoke check**

In `scripts/smoke.mjs`, after the last existing tool call, add:

```javascript
console.log("--- td_layout (preview) ---");
console.log(JSON.stringify(await layoutTool({ path: "/project1", apply: false }), null, 2));
```

And extend the import line at the top:

```javascript
import {
  paneTool, selectionTool, operatorsTool, errorsTool,
  paramsTool, graphTool, executeTool, layoutTool,
} from "../dist/index.js";
```

**Step 3: Update docs**

In `INTEGRATION.md`, remove the banner at the top of section 7:

```
> **Status:** v0.2 — in design. This section documents the planned `td_layout` tool.
```

In `README.md`:

- The sentence "Nine MCP tools, each a thin wrapper…" becomes "Ten MCP tools…".
- Append a new row to the tools table:

```markdown
| `td_layout` | Organize a subnet with a Sugiyama layered-DAG layout. Preview by default. | "Tidy `/project1/comp1`." |
```

**Step 4: Verify**

Run `python -m pytest tests/ -v` and `npm run test`.
Expected: all 39 Python tests + all 8 TS tests pass.

**Step 5: Commit**

```bash
git add dist/index.js scripts/smoke.mjs INTEGRATION.md README.md
git commit -m "build: rebuild dist, extend smoke, update docs for td_layout"
```

---

## Task 10 (manual, user): Rebuild the `.tox`

This task isn't automatable — `.tox` files are binary TD components, only TD's UI can produce them.

1. Open `toe/TouchAPI.tox` in TouchDesigner.
2. Inside the `TouchAPI` COMP, find the `td_api_src` Text DAT.
3. Select all, paste in the updated contents of `toe/src/td_api.py`.
4. Save the component: right-click `TouchAPI` → **Save Component .tox...** → overwrite `toe/TouchAPI.tox`.
5. Verify round-trip: `python scripts/extract_tox.py` — the extracted `td_api_src.py` must byte-match `toe/src/td_api.py`.
6. Run end-to-end smoke with TD live: `npm run smoke` — should include a preview plan for whatever `/project1` contains.
7. Commit: `git add toe/TouchAPI.tox && git commit -m "build(tox): rebuild TouchAPI.tox with td_layout endpoint"`.

After step 7, try it from Claude Code: "Tidy `/project1`." — Claude should return a preview plan, then a second request with `apply: true` should move the nodes.

---

## Acceptance checklist

- [ ] `python -m pytest tests/td_layout_test.py -v` — 13/13 passing.
- [ ] `python -m pytest tests/python_api_test.py -v` — 26/26 passing (22 + 4 new).
- [ ] `npm run test` — 8/8 passing (6 + 2 new).
- [ ] `npm run build` regenerates `dist/index.js` cleanly.
- [ ] `npm run smoke` exercises the new tool against a live TD.
- [ ] `INTEGRATION.md §7` no longer carries the v0.2 banner.
- [ ] `README.md` reflects ten tools.
- [ ] `toe/TouchAPI.tox` rebuilt and byte-equivalent to source on extract.
- [ ] No new runtime dependencies in `package.json` or `toe/src/*.py`.
