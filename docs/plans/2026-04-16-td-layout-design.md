# `td_layout` Design — v0.2

**Status:** approved 2026-04-16, ready for implementation planning.

**Goal:** Add a `td_layout` MCP tool that reads a TD subnet (or a selection within one) and returns (preview) or applies a Sugiyama-style layered DAG layout, preserving cycles as labelled back-edges.

User-facing contract lives in [`INTEGRATION.md §7`](../../INTEGRATION.md). This doc is the technical twin.

## Decision log

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Scope | per-subnet OR selection-within-parent | Explicit scope; matches how TD users work |
| 2 | Default mode | preview | Probeable, reversible; `apply: true` to mutate |
| 3 | Algorithm | Full Sugiyama with Brandes–Köpf coordinate assignment | Graphviz-grade output, no external dep |
| 4 | Cycle breaking | Greedy feedback-arc set, prefer `feedbackTOP` seams | Tool breaks where a human would; reported |
| 5 | Deps | Python stdlib only | Matches `td_api.py` invariant; auditable from inside TD |
| 6 | Surface | Single MCP tool `td_layout` | One tool, flags cover modes |
| 7 | Direction | `LR` default, `TB` opt-in | TD signal-flow convention |
| 8 | Recursion | None; user runs per-subnet | YAGNI; avoids blast-radius surprises |

## Architecture

Two new layers, split so the algorithm is testable without TD:

1. **`toe/src/td_layout.py`** — pure Sugiyama engine. Stateless. No TD imports. Takes plain dicts, returns plain dicts.
2. **`toe/src/td_api.py`** — adds `POST /layout` endpoint. Collects node/wire data from TD's op tree, delegates to `td_layout.py`, optionally writes `nodeX`/`nodeY` back.

```
Claude
  │ td_layout(args)
  ▼
TS wrapper (src/index.ts)   ── fetch POST /layout ──▶
  │
  ▼
Python handler (td_api.py)
  │ collect nodes + wires from op tree
  ▼
Layout engine (td_layout.py) ── pure stdlib
  │ returns {positions, broken_edges, stats}
  ▼
Python handler
  │ if apply: op.nodeX/nodeY = ...
  ▼ JSON response
Claude
```

## Data model

### Engine input (`td_layout.py`)

```python
Graph = {
    "nodes": [{"id": str, "is_feedback_top": bool}, ...],
    "edges": [{"from": str, "to": str}, ...],   # directed
    "direction": "LR" | "TB",
    "spacing": {"rank": int, "node": int},
}
```

### Engine output

```python
Layout = {
    "positions": {node_id: (x, y)},
    "broken_edges": [{"from": str, "to": str, "on_feedback_top": bool}, ...],
    "stats": {
        "nodes": int, "edges": int, "layers": int,
        "crossings_before": int, "crossings_after": int,
    },
}
```

### HTTP contract (`POST /layout`)

Request body:
```json
{
  "path": "/project1/comp1",
  "selection_only": false,
  "direction": "LR",
  "spacing": {"rank": 150, "node": 30},
  "apply": false
}
```

Response (preview):
```json
{
  "mode": "preview",
  "target": "/project1/comp1",
  "plan": [{"path": "...", "from": [x, y], "to": [x, y]}, ...],
  "broken_edges": [...],
  "stats": {...}
}
```

Response (apply): same shape with `"mode": "applied"`.

## Algorithm — Sugiyama, 4 phases

### Phase 1: Cycle break (feedback-arc set)

Feedback-arc set is NP-hard; we use a feedback-TOP-aware greedy approximation.

```
for each SCC of size > 1:
    while SCC still has a cycle:
        candidates      = edges in SCC whose removal breaks ≥1 cycle
        feedback_edges  = candidates touching a feedbackTOP node
        pick            = min(feedback_edges or candidates, key=edge_score)
        remove pick from working graph
        record in broken_edges (on_feedback_top = pick in feedback_edges)
```

`edge_score(e) = out_degree(e.from) - in_degree(e.to)` — break "heaviest" back-edges first when no feedbackTOP is in the SCC.

### Phase 2: Layer assignment (longest-path)

On the post-break DAG:

```
for n in topo_order(G):
    layer[n] = 0 if preds(n) empty else 1 + max(layer[p] for p in preds(n))
```

Result: `layers: list[list[node_id]]`, rank 0 = sources.

### Phase 3: Crossing reduction (barycentric sweeps)

1. Insert dummy nodes for every edge spanning >1 layer so all edges are one-layer.
2. 24 alternating sweep passes (down / up): each node's order within its layer = barycenter of its neighbours' positions in the adjacent layer.
3. Second-pass median refinement.
4. Keep the ordering with the lowest edge-crossing count observed across all sweeps.

### Phase 4: Coordinate assignment (Brandes–Köpf)

1. Mark Type-1 conflicts (inner-segment crossings).
2. Four vertical alignments: up-left, up-right, down-left, down-right, each producing a separate x-coordinate assignment.
3. Compute block x-coords by longest-path in the alignment graph, respecting node-spacing constraints.
4. Average the four alignments → final coords, balanced.
5. Map to `direction`:
   - `LR`: `x = layer * rank_spacing`, `y = order * node_spacing`
   - `TB`: swap.

Remove dummy nodes from the output.

## Testing strategy

Three test layers, all isolated:

### Layer 1 — `tests/td_layout_test.py` (pure Python, no TD)

Runs against `td_layout.py` directly:

- Empty graph → empty positions, zero stats.
- Single node → `{"n": (0, 0)}`.
- Two-node chain A→B → A at rank 0, B at rank 1.
- Diamond A→{B,C}→D → B, C on the same rank, D on the last.
- Cycle with a feedbackTOP → broken edge is on feedback TOP, `on_feedback_top = True`.
- Cycle without any feedbackTOP → broken edge picked by `edge_score`.
- 20-node random DAG (seeded) → `crossings_after <= crossings_before`.
- Asymmetric fan-out (A→[B,C,D,E,F], all →G) → G's x is near the barycenter of B..F's x values.
- Disconnected graph (two components) → components stacked vertically, no overlap.
- `direction: "TB"` → x/y axes swapped relative to `LR` output.

### Layer 2 — `tests/python_api_test.py` (extend existing)

Adds `POST /layout` cases against `td_api.py` with mocked `td_runtime`:

- `apply: false` → response has `"mode": "preview"`, no `setattr` on mocked ops.
- `apply: true` → `nodeX` / `nodeY` set on each op in the plan.
- `selection_only: true` → only selected children enter the engine input.
- Unknown `path` → 404.
- Missing auth → 401 (via existing middleware).

### Layer 3 — `tests/api.test.ts` (extend existing)

- `layoutTool({path})` sends POST /layout with correct JSON body.
- Response shape is passed through verbatim.
- `apply: true` is forwarded.

## Failure modes

| Case | Behaviour |
|---|---|
| Empty subnet | `{"plan": [], "broken_edges": [], "stats": {"nodes": 0, ...}}` |
| Disconnected graph | Components stacked vertically, spaced by `node * 2` |
| Graph too large (>500 nodes) | Return `{"error": "graph too large", "node_count": N}` — user should target smaller scope |
| Selection spans ranks unevenly | Selected set is laid out as its own graph; non-selected node positions are preserved |
| Operator with no `nodeX` attr | Silently skipped in apply; reported in `skipped: [...]` |

## Out of scope for v0.2

- Annotation / colour / name-prefix clustering → possible v0.3
- Recursive layout of nested COMPs → user calls per-subnet
- Expression-reference edges → never; wires only
- Alternative algorithms (force-directed, tree) → don't ship until needed

## Risks

- **Brandes–Köpf is ~200 LOC of fiddly code.** Mitigation: test-driven from canonical papers, use well-known reference implementations (e.g. d3-dag, dagre) as oracles for small cases.
- **Cycle-break heuristic may break "wrong" edge** when multiple feedbackTOPs exist in one SCC. Mitigation: `broken_edges` is always in the response, so the user can diff and complain.
- **Large graphs may be slow.** The 500-node cap is defensive; real TD subnets rarely exceed 50 nodes.

## Acceptance criteria

1. `npm run test` passes, including new TS test.
2. `python -m pytest tests/python_api_test.py tests/td_layout_test.py` passes.
3. `npm run smoke` exercises `layoutTool` preview against a live TD (extend `scripts/smoke.mjs`).
4. `INTEGRATION.md §7` v0.2 banner removed; section reflects shipped behaviour.
5. No new runtime dependencies.
