# Tools

Which of the 25 Touch MCP tools to reach for, and in what order. The heuristic: **read before write, structured before scripted, verify after every batch.**

## The 25 tools at a glance

| Tool | Purpose | Mutates? |
|------|---------|----------|
| `td_project` | Project/TD version, timeline state; play/pause/frame/rate/save | both |
| `td_pane` | Which network pane is active, what path | read |
| `td_selection` | Currently selected ops | read |
| `td_operators` | List direct children of a path | read |
| `td_find` | Recursive search: name/type glob, family, has-errors | read |
| `td_graph` | Walk the node graph from a path | read |
| `td_info` | One op in depth: in/out wiring, flags, errors, cook, family facts | read |
| `td_types` | Exact creatable type names (for `td_create`) | read |
| `td_params` | Read (filtered / detailed) **or** write parameters | both |
| `td_chop` | Sample channel values off a CHOP | read |
| `td_dat` | Read a DAT's table cells + raw text | read |
| `td_errors` | Errors/warnings under a path | read |
| `td_perf` | Slowest-cooking ops by cook time | read |
| `td_screenshot` | Grab image from a TOP (png or jpg) | read |
| `td_create` | Create a new op (with inputs + params) | write |
| `td_copy` | Duplicate an op or whole COMP | write |
| `td_node` | Rename / move / color / comment / bypass-display-render-lock flags | write |
| `td_dat_write` | Replace DAT text (shaders, scripts) or table rows | write |
| `td_connect` | Wire one op's output into another's input | write |
| `td_disconnect` | Drop wires into an op's input(s) | write |
| `td_delete` | Destroy an op | write |
| `td_bind` | Bind a parameter to an expression / constant | write |
| `td_custom_par` | Add a custom parameter to a COMP | write |
| `td_layout` | Auto-arrange a subnet (preview, or `apply`) | both |
| `td_execute` | Run arbitrary Python in TD | write (arbitrary) |

## Keeping output small

Large projects produce large responses. Narrow before you read:

- `td_find` over `td_graph` when you know roughly what you want (`name: "glsl*"`, `family: "TOP"`, `errors: true`).
- `td_params` with `nondefault: true` shows only what someone actually changed. That's usually the interesting part of a 200-parameter op. Add `names: ["res*"]` to focus, and `detail: true` only when you need modes, expressions, ranges, or menu options.
- `td_errors` with `path` scoped to the subnet you're working in.
- `td_screenshot` with `format: "jpg"` for quick visual checks. Save PNG for when exact pixels matter.

## Debugging one node: `td_info`

When a specific op looks wrong, `td_info` is the first call. It shows both upstream **and downstream** wiring (`td_graph` only gives inputs), whether the op is bypassed or locked, its errors, how long it cooks, and family facts: a TOP's actual resolution, a CHOP's channel names and sample rate, a SOP's point count, a COMP's custom parameter pages.

## Don't guess type names: `td_types`

`td_create` needs exact type names, and TD's are irregular (`audiofileinCHOP`, `glslmultiTOP`, `textDAT`). If you're not sure, call `td_types` with `filter`. `td_create` also suggests close matches when a name is wrong.

## Writing shaders and scripts: `td_dat_write`

GLSL code, Python extensions, and config tables live in DATs. `td_dat_write` with `text` replaces the whole body, which is cleaner and more auditable than a `td_execute` that assigns `.text`. After writing a shader, call `td_info` or `td_errors` on the **GLSL op** (not the DAT): compile errors show up there.

## Building reusable components: `td_custom_par` + `td_bind`

To give a COMP a proper control panel:

1. `td_custom_par` on the COMP, e.g. `{name: "Speed", style: "float", min: 0, max: 10, default: 1}`. Names get normalized to TD's `Capitalized` form, and the response tells you the final name.
2. `td_bind` the inner ops' parameters to it: `expr: "parent().par.Speed"`.

## Tidying layouts with `td_layout`

After building or rewiring a subnet, the new ops pile up at the origin. `td_layout`
runs a layered-DAG (Sugiyama) arrange over the direct children of `path`:

- It **previews by default** — returns a `plan` of `from`/`to` positions, plus
  `broken_edges` (wires it would cut to break cycles) and `overlaps`, without
  moving anything. Pass `apply: true` to actually write `nodeX`/`nodeY`.
- Scope it with `exclude` (names or paths to leave put — e.g. the `TouchAPI`
  COMP itself) or `selection_only`. Use `direction: "TB"` for top-to-bottom.

## Seeing the data, not just the graph

`td_graph` shows you *topology*; it does not show you the numbers moving through it. When a network is wired correctly but the output is wrong, the bug is almost always in the data:

- **`td_chop`** — read the actual channel values. Is `audioanalysis1` outputting zeros? Is the LFO in the range you expect? This is the CHOP equivalent of `td_screenshot` for TOPs.
- **`td_dat`** — read table/text DAT contents (instance data, OSC tables, config).
- **`td_perf`** — when the project is dropping frames, find the operator eating the cook budget before you start guessing.

## Reactivity: `td_bind` vs `td_params`

`td_params` normally sets **static** values. `td_bind` makes a parameter **reactive**:

- `mode: "expression"` (default) sets `param.expr`, e.g. `op('audio1')['rms']` to drive a value off a channel, or `absTime.seconds * 0.1` for time-based motion. This is the idiomatic TD way to link parameters to signals.
- `mode: "constant"` sets a fixed `val` and forces the parameter back to constant mode (use to *un*-bind).

`td_params` can also do both in one batch: `{ tx: { expr: "absTime.seconds" }, ty: 2 }`. A plain value on an expression-driven parameter switches it back to constant. Writing `true` to a pulse parameter (`reset`, `reload`, `resetpulse`, ...) fires it.

Reach for `td_bind` whenever the intent is "make X follow Y" rather than "set X to a number".

## Editing existing networks

`td_create` wires on creation, but to refactor what's already there:

- **`td_connect` / `td_disconnect`**: rewire without recreating ops. `inputIndex` selects which input (0-based). Omit it on disconnect to clear all inputs.
- **`td_node`**: rename, reposition, color-code, comment, or flip flags. Bypassing (`bypass: true`) is the non-destructive way to A/B test an effect. Prefer it to deleting.
- **`td_copy`**: duplicate an op or a whole COMP subnet, e.g. to branch a variation before experimenting.
- **`td_delete`**: destroy an op. This can't be undone from the API (TD's in-app Undo still covers it). Always check `td_info` first so you know what the op feeds.

## Hierarchy of preference for mutation

1. **`td_create`** when creating new ops. It's atomic, takes `inputs` + `params`, auto-places the op next to its input, and doesn't need Python.
2. **`td_params`** when changing parameters. Express your intent as a `{name: value}` dict. This is auditable, because the tool call is the diff.
3. **`td_node` / `td_dat_write` / `td_custom_par`** for flags, names, DAT contents, and component controls.
4. **`td_execute`** only when none of the above can express what you want. Examples that justify it: bulk refactors across many ops, or calling op-specific methods like `op('x').cook(force=True)`. It returns the last expression's value as `result`, so an inspection one-liner like `len(op('/project1').children)` needs no `print`.

Rule of thumb: if the thing you want is "set parameter X on op Y", use `td_params`. If your Python is three lines or fewer and two of them set params, use `td_params`.

## Reading before writing

Before mutating a subnet you haven't touched this session:

1. `td_pane`: where am I?
2. `td_graph` (small subnets) or `td_find` (large ones) on the target path: what's already there?
3. `td_info` on the ops you'll touch: what feeds them, what they feed.
4. `td_params` with `nondefault: true` on any op whose config matters: learn what's already set.

Skipping this step is the most common source of wrong-result mutations: overwriting state, creating duplicate ops, wiring to the wrong input index.

## Verification after writing

After any batch of mutations:

1. `td_errors` — catches GLSL compile failures, missing-input warnings, bad parameter expressions. TD does **not** throw these up front; they sit in the error log.
2. `td_screenshot` on the project's main Render TOP (or the TOP you're working on). This is the single most valuable feedback signal — the output is a real image and your eye catches things no text report will.

If errors are clean and the screenshot looks right, commit. If not, iterate.

## `td_execute` safety notes

- `me` inside the executed code refers to the `from_op` context you passed. If you didn't pass one, it's `/`, which is rarely what you want.
- Prefer absolute paths (`op('/project1/noise1')`) over relative (`op('../noise1')`) when scripting from MCP — you don't always know what `me` is.
- Don't run destructive ops (`op.destroy()`, `parent().destroyChildren()`) without first listing what you're about to kill.
- Long `td_execute` bodies are fragile: a single typo nukes the batch. Break into multiple `td_create`/`td_params` calls when possible.

## Canonical session shape

```
td_pane                               # orient
td_graph /project1                    # map the territory (td_find on big projects)
td_types --filter noise               # get exact type names
td_create ... inputs=[...] params={}  # build
td_params ...                         # tune
td_errors /project1                   # did anything break?
td_screenshot /project1/render1 jpg   # does it look right?
td_layout /project1 apply             # tidy up
```

## Common pitfalls

- Reaching for `td_execute` on reflex for things `td_params` handles cleanly. Costs auditability.
- Skipping `td_errors` after GLSL edits — output silently goes black or stale.
- Skipping `td_screenshot` on visual work — text reports lie about visual correctness.
- Running `td_create` with a `name` that already exists (returns a 409 `NameTaken`). Letting TD auto-name is fine, because the response contains the real path, so use that instead of guessing the suffix.
- Guessing an operator type name. Use `td_types`.
- Checking the shader DAT for GLSL errors instead of the GLSL TOP/MAT that compiles it.
- Using `td_graph` on a huge subnet and drowning in output. Start at the level you care about; descend only as needed.
