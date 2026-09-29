---
name: td-guide
description: Use when building or modifying TouchDesigner networks — covers operator families, rendering, GLSL, instancing, feedback, and which MCP tool to reach for.
---

# td-guide

Reference for building TouchDesigner networks with the Touch MCP tools.

## Quick tool selection

- Want to **orient**? `td_project` (TD version, timeline), `td_pane`, `td_selection`.
- Want to **read** topology? `td_operators`, `td_graph`, or `td_find` (recursive search by name/type/family/errors) on big projects.
- Debugging **one node**? `td_info`: inputs and outputs, flags, errors, cook time, resolution, and channel names in one call.
- Want to **read** the data flowing through it? `td_chop` (channel values), `td_dat` (table cells), `td_params` (use `nondefault: true` to see only what changed).
- Want to **create** an op? `td_types` for the exact type name, then `td_create` with `inputs` and `params`.
- Want to **rewire / remove / duplicate**? `td_connect`, `td_disconnect`, `td_delete`, `td_copy`.
- Want to **rename, move, bypass, or color** a node? `td_node`.
- Writing a **shader, script, or table**? `td_dat_write`, then check the GLSL op with `td_info`/`td_errors`.
- Building a **reusable component**? `td_custom_par` on the COMP, then `td_bind` inner params to `parent().par.Name`.
- Just built a net and it's a tangled pile at the origin? `td_layout` (preview first, then `apply: true`).
- Want to **tweak** parameters? `td_params` with a `params: {...}` object. Avoid `td_execute` for simple tweaks.
- Want a parameter to **react** to a signal (audio, LFO, time)? `td_bind` with an `expr`, or `td_params` with `{ par: { expr: "..." } }`.
- Chasing dropped frames? `td_perf` to find the slowest-cooking op.
- Need something unusual? `td_execute` runs full Python inside TD with the textport's globals. `me` is the `from_op` context, and the last expression's value comes back as `result`.
- After significant changes, **screenshot** a Render TOP with `td_screenshot` (`format: "jpg"` for quick checks) and read errors with `td_errors`.

## Loop of operation

`td_pane` → `td_graph`/`td_find` → mutate (`td_create` / `td_params`) → `td_errors` → `td_screenshot`. Repeat.

Before mutating anything, read the graph first. Blind writes produce wrong nets.
After mutating, always check errors and eyeball the output. TD fails silently on a surprising number of things (missing inputs, shader link errors, wrong cook order).

## Index

- [operators](reference/operators.md) — SOP/POP/TOP/CHOP/DAT/COMP/MAT families, common ops
- [rendering](reference/rendering.md) — Camera / Light / Render TOP / Geometry COMP pipeline
- [glsl](reference/glsl.md) — GLSL TOP, GLSL MAT, GLSL POP — I/O and uniforms
- [instancing](reference/instancing.md) — Geometry COMP instancing from CHOP/DAT/SOP
- [feedback](reference/feedback.md) — feedback TOP loops, frame delay, reset strategy
- [tools](reference/tools.md) — deeper notes on when to pick which MCP tool
