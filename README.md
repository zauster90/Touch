# Touch

A Claude Code plugin that exposes TouchDesigner to Claude via MCP: execute Python, query the network editor, inspect operators and parameters, capture TOP renders, and create/wire operators programmatically.

## Status

v0.4 — local-only install. Requires TouchDesigner 2025+ and Node 20+. Developed and tested on Windows 11. POSIX paths are handled but unverified end-to-end.

> **Upgrading to v0.4:** re-run [`toe/install.py`](toe/install.py) (or re-save your `.tox`). The eight new tools are new server endpoints — an old `TouchAPI` answers them with 404. The shim also now bridges `families`/`project`/`app`, which `td_types` and `td_project` need.
>
> **Upgrading from v0.1/v0.2:** rebuild the `TouchAPI` component — easiest via [`toe/install.py`](toe/install.py). v0.3 marshals every request onto TD's main cook thread (`bootstrap.onFrameStart` → `drain_main_queue`), which is required for safe op access; it also adds a `td_layout` DAT and the `ParMode` shim bridge. An older `.tox` that lacks the `onFrameStart` drain will accept requests but time them out.

## What it does

Twenty-five MCP tools, each a thin wrapper over a `127.0.0.1`-bound HTTP endpoint hosted inside TouchDesigner.

**Orient & discover**

| Tool | Description | Example prompt |
|---|---|---|
| `td_project` | Project/TD version info, timeline state; play/pause, jump frame, set rate, save. | "Pause the timeline and go to frame 1." |
| `td_pane` | Current network editor pane: path, pan, zoom. | "What network am I looking at?" |
| `td_selection` | Operators currently selected. | "What's selected?" |
| `td_operators` | List children of an operator path. | "List the TOPs under `/project1`." |
| `td_find` | Recursive search by name/type glob, family, or "has errors". | "Find every GLSL TOP in the project." |
| `td_graph` | Structured JSON subgraph: nodes + wires, to a given depth. | "Show me the graph under `/project1/comp1`." |
| `td_info` | One op in depth: inputs *and* outputs, flags, errors, cook stats, resolution / channel names / point counts. | "Why is `render1` black?" |
| `td_types` | Exact creatable type names (e.g. `audiofileinCHOP`). | "What noise operators exist?" |

**Read data**

| Tool | Description | Example prompt |
|---|---|---|
| `td_params` | Read (filter by name, non-default only, or full detail) or patch parameters; supports expressions and pulses. | "Set `render1.resolutionw` to 1920." |
| `td_chop` | Sample channel data off a CHOP (downsampled, capped). | "What values is `audioanalysis1` outputting?" |
| `td_dat` | Read a DAT's table cells and raw text. | "Dump the `table1` DAT." |
| `td_errors` | Errors and warnings under a path (default: whole project). | "What errors are in the project?" |
| `td_perf` | Slowest-cooking operators under a path, by cook time. | "What's the slowest op in `/project1`?" |
| `td_screenshot` | PNG or JPEG of a TOP's current frame, returned as an inline image. | "Screenshot the `out1` render TOP." |

**Build & edit**

| Tool | Description | Example prompt |
|---|---|---|
| `td_create` | Create an operator, optionally wired to inputs and with params set, auto-placed after its input. | "Add a `noiseTOP` and wire it into `render1`." |
| `td_copy` | Duplicate an op (or a whole COMP subnet). | "Make a copy of `fx_chain`." |
| `td_node` | Rename, move, recolor, comment, or toggle bypass/display/render/lock/viewer. | "Bypass `blur1`." |
| `td_dat_write` | Replace a DAT's text (shaders, scripts) or table rows. | "Rewrite the pixel shader to add a vignette." |
| `td_connect` | Wire one operator's output into another's input. | "Wire `blur1` into `comp1`'s second input." |
| `td_disconnect` | Drop wires into an operator's input(s). | "Disconnect `comp1`'s inputs." |
| `td_delete` | Destroy an operator. | "Delete `noise2`." |
| `td_bind` | Bind a parameter to an expression (reactive) or constant. | "Drive `geo1.tx` off `op('lfo1')['chan1']`." |
| `td_custom_par` | Add a custom parameter (float, menu, rgb, pulse, ...) to a COMP. | "Give `fx_chain` a Speed slider from 0 to 10." |
| `td_layout` | Auto-arrange a subnet (layered DAG / Sugiyama), untangling wire crossings. | "Tidy up the layout under `/project1`." |
| `td_execute` | Run arbitrary Python inside TD with textport globals. Returns stdout, stderr, and the last expression's value. | "How many instances is `geo1` drawing?" |

## Security posture

- Bound to `127.0.0.1` only — never reachable off the loopback interface.
- Bearer-token auth on every request; token stored at `%APPDATA%\claude-td\token` (Windows) or `~/.config/claude-td/token` (POSIX), `0600` on POSIX.
- `Host` header whitelist (`localhost`, `127.0.0.1`) blocks DNS-rebinding attacks.
- No CORS headers emitted — browsers cannot cross-origin this server.
- Token rotation is one click on the `TouchAPI` component.
- All requests logged (last 500) in TD; queryable from inside TD.

## Install

1. In Claude Code, run:
   ```
   /plugin install C:/path/to/Touch
   ```
   (Use the absolute path to your local clone of this repo. There is no marketplace listing.)
2. Open a TouchDesigner project, then drop the TouchAPI component into the network (typically `/project1`):
   - **If you have a built `toe/TouchAPI.tox`** (you saved one previously, or got it from a release): drag it straight in. Done.
   - **Otherwise** — drag `toe/install.py` into the network (TD makes a Text DAT from it), then right-click that DAT → **Run Script**. It builds the whole `TouchAPI` component, wires everything, and starts the server in one step. No Component Editor, no hand-set parameters. See [`toe/BUILD_TOX.md`](toe/BUILD_TOX.md) for what it does under the hood.
3. Select the `TouchAPI` node and verify the `Status` custom parameter reads:
   ```
   READY @ 127.0.0.1:44444
   ```
   If it reads `stopped`, the server didn't bind — check TD's textport for Python errors.

The first time the component runs, TD writes a fresh token to the config path above. The MCP server reads that same file on startup.

> **Want a one-drag binary for everyone else?** After `install.py` reports READY, right-click the `TouchAPI` COMP → **Save Component .tox...**, save it as `toe/TouchAPI.tox`, and commit it. From then on, anyone can skip the script and just drag the `.tox` in (step 2, first bullet).

## Usage examples

Once the plugin is installed and the component is running, in Claude Code:

- "Show me the current network."
- "Add a noise TOP and wire it into the render."
- "What errors are in the project?"
- "Screenshot the `out1` render TOP."

## Token rotation

1. Select the `TouchAPI` node in TD.
2. Press the **Rotate token** pulse parameter. TD regenerates the token file and restarts the HTTP server on the new secret.
3. That's it — the MCP server re-reads the token file on every request, so the next tool call picks up the new token. No Claude Code restart needed.

## Development

Clone, install, test, build:

```bash
npm install
npm run test        # 23 TS unit tests (client wrapper)
python -m pytest tests/python_api_test.py tests/layout_test.py   # 83 Python tests (server core + layout)
npm run build       # bundles src/index.ts -> dist/index.js
npm run smoke       # exercises the read-only tools against a live TD (see tests/manual.md)
```

Regenerating the `.tox`: see [`toe/BUILD_TOX.md`](toe/BUILD_TOX.md). After rebuilding, audit the `.tox` with:

```bash
python scripts/extract_tox.py
```

The extractor uses `toeexpand` if available to dump DAT text, which should byte-for-byte match `toe/src/td_api.py`. Any drift is a reason to reject the `.tox`.

## Layout

```
Touch/
├── .claude-plugin/plugin.json   # plugin manifest
├── .mcp.json                    # MCP server declaration
├── src/index.ts                 # MCP server + 25 tool wrappers (TS)
├── dist/index.js                # bundled output (built)
├── toe/
│   ├── install.py              # one-shot in-TD builder for the TouchAPI component
│   ├── TouchAPI.tox            # optional built binary (produce via Save Component .tox)
│   ├── src/td_api.py           # authoritative Python server source
│   ├── src/td_layout.py        # pure-stdlib layered-DAG layout (no TD deps)
│   └── BUILD_TOX.md            # install.py fast path + manual build recipe
├── tests/
│   ├── api.test.ts              # TS client unit tests
│   ├── python_api_test.py       # Python server unit tests
│   └── manual.md                # end-to-end checklist (run with real TD)
├── scripts/
│   ├── smoke.mjs                # smoke every tool against a live server
│   └── extract_tox.py           # audit the .tox vs td_api.py
├── skills/                      # Claude Code skills bundled with the plugin
├── docs/plans/                  # design + implementation plans
└── README.md
```

## License

MIT. See [`LICENSE`](LICENSE).
