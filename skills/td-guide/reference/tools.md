# Tools

Which of the 17 Touch MCP tools to reach for, and in what order. The heuristic: **read before write, structured before scripted, verify after every batch.**

## The 17 tools at a glance

| Tool | Purpose | Mutates? |
|------|---------|----------|
| `td_pane` | Which network pane is active, what path | read |
| `td_selection` | Currently selected ops | read |
| `td_graph` | Walk the node graph from a path | read |
| `td_operators` | List ops in a path / filter by family | read |
| `td_params` | Read **or** write parameters on an op | both |
| `td_chop` | Sample channel values off a CHOP | read |
| `td_dat` | Read a DAT's table cells + raw text | read |
| `td_perf` | Slowest-cooking ops by cook time | read |
| `td_errors` | Compile/runtime errors surfaced by TD | read |
| `td_screenshot` | Grab image from a TOP | read |
| `td_create` | Create a new op (with inputs + params) | write |
| `td_connect` | Wire one op's output into another's input | write |
| `td_disconnect` | Drop wires into an op's input(s) | write |
| `td_delete` | Destroy an op | write |
| `td_bind` | Bind a parameter to an expression / constant | write |
| `td_layout` | Auto-arrange a subnet (preview, or `apply`) | both |
| `td_execute` | Run arbitrary Python in TD | write (arbitrary) |

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

`td_params` sets a **static** value. `td_bind` makes a parameter **reactive**:

- `mode: "expression"` (default) sets `param.expr`, e.g. `op('audio1')['rms']` to drive a value off a channel, or `absTime.seconds * 0.1` for time-based motion. This is the idiomatic TD way to link parameters to signals.
- `mode: "constant"` sets a fixed `val` and forces the parameter back to constant mode (use to *un*-bind).

Reach for `td_bind` whenever the intent is "make X follow Y" rather than "set X to a number".

## Editing existing networks

`td_create` wires on creation, but to refactor what's already there:

- **`td_connect` / `td_disconnect`** — rewire without recreating ops. `inputIndex` selects which input (0-based); omit it on disconnect to clear all inputs.
- **`td_delete`** — destroy an op. Irreversible from the API (TD's in-app Undo still covers it). Always `td_graph` first so you know what feeds the op you're about to remove.

## Hierarchy of preference for mutation

1. **`td_create`** when creating new ops. It's atomic, takes `inputs` + `params`, and doesn't need Python.
2. **`td_params`** when changing parameters. Express your intent as a `{name: value}` dict. This is auditable — the tool call is the diff.
3. **`td_execute`** only when 1 and 2 can't express what you want. Examples that justify it: bulk refactors across many ops, creating a custom COMP with internal wiring, deleting ops, calling op-specific methods like `op('x').cook(force=True)`.

Rule of thumb: if the thing you want is "set parameter X on op Y", use `td_params`. If your Python is three lines or fewer and two of them are setting params, use `td_params`.

## Reading before writing

Before mutating a subnet you haven't touched this session:

1. `td_pane` — where am I?
2. `td_graph` on the target path — what's already there?
3. `td_operators` with family filter if you need a list.
4. `td_params` on any op whose config matters — learn what's already set.

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
td_graph /project1                    # map the territory
td_operators /project1 --family top   # see what exists
td_create ...                         # build
td_params ...                         # tune
td_errors                             # did anything break?
td_screenshot /project1/render1       # does it look right?
```

## Common pitfalls

- Reaching for `td_execute` on reflex for things `td_params` handles cleanly. Costs auditability.
- Skipping `td_errors` after GLSL edits — output silently goes black or stale.
- Skipping `td_screenshot` on visual work — text reports lie about visual correctness.
- Running `td_create` with a `name` that already exists (errors) instead of letting TD auto-name, or vice versa, relying on auto-name when you then need to reference the op (can't guess the suffix).
- Using `td_graph` on a huge subnet and drowning in output. Start at the level you care about; descend only as needed.
