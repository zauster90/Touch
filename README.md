# Touch

A Claude Code plugin that exposes TouchDesigner to Claude via MCP: execute Python, query the network editor, inspect operators and parameters, capture TOP renders, and create/wire operators programmatically.

## Status

v0.1 — local-only install. Requires TouchDesigner 2025+ and Node 20+. Developed and tested on Windows 11. POSIX paths are handled but unverified end-to-end.

## What it does

Nine MCP tools, each a thin wrapper over a `127.0.0.1`-bound HTTP endpoint hosted inside TouchDesigner:

| Tool | Description | Example prompt |
|---|---|---|
| `td_execute` | Run arbitrary Python inside TD. Returns stdout, stderr, and the last expression value. | "Set `op('/project1/geo1').par.tx` to 5." |
| `td_pane` | Current network editor pane: path, pan, zoom. | "What network am I looking at?" |
| `td_selection` | Operators currently selected. | "What's selected?" |
| `td_operators` | List children of an operator path. | "List the TOPs under `/project1`." |
| `td_params` | Read or patch parameters on any operator. | "Set `render1.resolutionw` to 1920." |
| `td_errors` | All project errors and warnings, walked from root. | "What errors are in the project?" |
| `td_screenshot` | PNG of a TOP's current frame, returned as an inline image. | "Screenshot the `out1` render TOP." |
| `td_graph` | Structured JSON subgraph: nodes + wires, to a given depth. | "Show me the graph under `/project1/comp1`." |
| `td_create` | Create an operator of a given type under a parent, optionally wired to inputs. | "Add a `noiseTOP` and wire it into `render1`." |

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
2. Open a TouchDesigner project, then drag `toe/TouchAPI.tox` into the network (typically `/project1`).
3. Select the `TouchAPI` node and verify the `Status` custom parameter reads:
   ```
   READY @ 127.0.0.1:44444
   ```
   If it reads `stopped`, the server didn't bind — check TD's textport for Python errors.

The first time you drop the .tox in, TD writes a fresh token to the config path above. The MCP server reads that same file on startup.

## Usage examples

Once the plugin is installed and the .tox is running, in Claude Code:

- "Show me the current network."
- "Add a noise TOP and wire it into the render."
- "What errors are in the project?"
- "Screenshot the `out1` render TOP."

## Token rotation

1. Select the `TouchAPI` node in TD.
2. Press the **Rotate token** pulse parameter. TD regenerates the token file and restarts the HTTP server on the new secret.
3. Restart Claude Code. The MCP server reads the token once at startup and caches it — a rotation without a restart will produce 401s.

## Development

Clone, install, test, build:

```bash
npm install
npm run test        # 6 TS unit tests (client wrapper)
python -m pytest tests/python_api_test.py   # 22 Python tests (server core)
npm run build       # bundles src/index.ts -> dist/index.js
npm run smoke       # exercises 7 tools against a live TD (see tests/manual.md)
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
├── src/index.ts                 # MCP server + 9 tool wrappers (TS)
├── dist/index.js                # bundled output (built)
├── toe/
│   ├── TouchAPI.tox             # TD component, built from toe/src/td_api.py
│   ├── src/td_api.py            # authoritative Python server source
│   └── BUILD_TOX.md             # how to rebuild the .tox from src
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
