---
name: td-guide
description: Use when building or modifying TouchDesigner networks — covers operator families, rendering, GLSL, instancing, feedback, and which MCP tool to reach for.
---

# td-guide

Reference for building TouchDesigner networks with the Touch MCP tools.

## Quick tool selection

- Want to **read** topology? `td_pane`, `td_selection`, `td_operators`, `td_graph`.
- Want to **read** the data flowing through it? `td_chop` (channel values), `td_dat` (table cells), `td_params` (parameter values).
- Want to **create** an op? `td_create` (supports `inputs` array for wiring on create).
- Want to **rewire / remove**? `td_connect`, `td_disconnect`, `td_delete`.
- Just built a net and it's a tangled pile at the origin? `td_layout` (preview first, then `apply: true`).
- Want to **tweak** parameters? `td_params` with a `params: {...}` object. Avoid `td_execute` for simple tweaks.
- Want a parameter to **react** to a signal (audio, LFO, time)? `td_bind` with an `expr` — not `td_params`.
- Chasing dropped frames? `td_perf` to find the slowest-cooking op.
- Need something unusual? `td_execute` — full Python inside TD. `me` refers to the `from_op` context.
- After significant changes, **screenshot** a Render TOP with `td_screenshot` and read errors with `td_errors`.

## Loop of operation

`td_pane` → `td_graph` → mutate (`td_create` / `td_params`) → `td_errors` → `td_screenshot`. Repeat.

Before mutating anything, read the graph first. Blind writes produce wrong nets.
After mutating, always check errors and eyeball the output — TD fails silently on a surprising number of things (missing inputs, shader link errors, wrong cook order).

## Index

- [operators](reference/operators.md) — SOP/POP/TOP/CHOP/DAT/COMP/MAT families, common ops
- [rendering](reference/rendering.md) — Camera / Light / Render TOP / Geometry COMP pipeline
- [glsl](reference/glsl.md) — GLSL TOP, GLSL MAT, GLSL POP — I/O and uniforms
- [instancing](reference/instancing.md) — Geometry COMP instancing from CHOP/DAT/SOP
- [feedback](reference/feedback.md) — feedback TOP loops, frame delay, reset strategy
- [tools](reference/tools.md) — deeper notes on when to pick which MCP tool
