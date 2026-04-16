---
name: td-guide
description: Use when building or modifying TouchDesigner networks — covers operator families, rendering, GLSL, instancing, feedback, and which MCP tool to reach for.
---

# td-guide

Reference for building TouchDesigner networks with the Touch MCP tools.

## Quick tool selection

- Want to **read** state? `td_pane`, `td_selection`, `td_operators`, `td_params`, `td_errors`, `td_graph`, `td_screenshot`.
- Want to **create** an op? `td_create` (supports `inputs` array for wiring on create).
- Want to **tweak** parameters? `td_params` with a `params: {...}` object. Avoid `td_execute` for simple tweaks.
- Need something unusual? `td_execute` — full Python inside TD. `me` refers to the `from_op` context.
- After significant changes, **screenshot** a Render TOP with `td_screenshot` to verify visually.

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
