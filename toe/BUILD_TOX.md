# Building `TouchAPI.tox`

How to construct or rebuild the `TouchAPI` component from scratch in TouchDesigner using the authoritative source at [`toe/src/td_api.py`](src/td_api.py).

The `.tox` is a binary TD component. It can't be generated from a script *outside* TD — TD's UI is the build tool. But you don't have to assemble it by hand: an installer script automates every step below from inside TD.

## Fast path: `install.py` (recommended)

1. Drag [`toe/install.py`](install.py) into a TD network (TD creates a Text DAT from it).
2. Right-click that DAT → **Run Script**.

It creates `/project1/TouchAPI`, adds the four DATs (`td_api_src`, `td_layout`, `td_runtime_shim`, `bootstrap`) plus the `param_exec` token-rotation callback and the custom parameters, then starts the server — the `Status` parameter should read `READY @ 127.0.0.1:44444`. Re-running it cleanly rebuilds (it stops the old server first, so the port frees up).

The installer reads `td_api.py` and `td_layout.py` from [`toe/src/`](src/) on disk, so there is no second copy to drift. If you pasted it into the textport instead of dragging the file (so it has no `file` parameter to locate itself by), set `TOE_DIR_OVERRIDE` at the top of the script.

To produce a distributable binary: after it reports READY, right-click `TouchAPI` → **Save Component .tox...** → save as `toe/TouchAPI.tox`, then verify the round-trip (§7) and commit. After that, others can skip the script and just drag the `.tox` in.

---

The rest of this document is the **manual recipe** — what `install.py` does, step by step. Read it to understand the component or to build it by hand.

## 1. Open TouchDesigner

Launch TD 2025 or newer. Create a new, empty project. Work inside `/project1`.

## 2. Create a Container COMP

- `Tab` in the network → add a **Container COMP**.
- Rename it to `TouchAPI`. The final path will be `/project1/TouchAPI`.
- Dive inside it (`i`).

## 3. Add the DATs

Inside `/project1/TouchAPI`:

### 3a. `td_api_src` — Text DAT (source of truth)

- `Tab` → **Text DAT**. Rename to `td_api_src`.
- On the DAT's `Common` page, set **Language** to `Python`.
- Open the DAT and paste the full contents of `toe/src/td_api.py`.
- Save. Do not hand-edit this DAT after this point — always edit `toe/src/td_api.py` in-repo and re-sync.

### 3b. `td_layout` — Text DAT (layout engine)

- `Tab` → **Text DAT**. Rename to `td_layout`.
- Set **Language** to `Python`.
- Paste the full contents of `toe/src/td_layout.py`. It is pure stdlib (no TD imports) and is imported by `td_api_src`'s `/layout` endpoint.
- Like `td_api_src`, treat this DAT as read-only — edit `toe/src/td_layout.py` in-repo and re-sync.

### 3c. `td_runtime_shim` — Text DAT (runtime bridge)

- `Tab` → **Text DAT**. Rename to `td_runtime_shim`.
- Set **Language** to `Python`.
- Paste this exactly:
  ```python
  # td_runtime shim — exposes TD's globals as a module so td_api_src
  # can `import td_runtime` both inside TD and in standalone tests.
  import sys, types
  m = types.ModuleType("td_runtime")
  m.op = op
  m.ops = ops
  m.ui = ui
  m.ParMode = ParMode  # needed by /bind to switch a parameter to expression mode
  sys.modules["td_runtime"] = m
  ```
  > If you built the `.tox` against an older shim that did not bridge `ParMode`,
  > `td_bind` still writes the expr/val but leaves the parameter's mode unchanged.
  > Re-paste this shim and resave to get the mode switch.
- Right-click the DAT → **Run Script**. This must run once before `td_api_src` is imported; the `bootstrap` Execute DAT (below) re-runs it on project start.

### 3d. `bootstrap` — Execute DAT (lifecycle)

- `Tab` → **Execute DAT**. Rename to `bootstrap`.
- On the DAT's parameters, turn on **Start**, **Exit**, and **Frame Start** callbacks.
- Replace the body with:
  ```python
  import sys

  def onStart():
      op('td_runtime_shim').run()
      sys.modules['td_layout'] = mod('td_layout')
      mod('td_api_src').start_server()
      parent().par.Status = f"READY @ 127.0.0.1:{parent().par.Port.eval()}"

  def onExit():
      try:
          mod('td_api_src').stop_server()
      finally:
          parent().par.Status = "stopped"

  def onFrameStart(frame):
      mod('td_api_src').drain_main_queue()
  ```
  `mod('td_api_src')` accesses a Python Text DAT as a module (TD's DAT-as-module feature).

  **Why `onFrameStart` matters:** TD's Python API (`op()`, `ui.*`, parameter
  access) is only safe on the main cook thread. The HTTP server runs request
  handlers on worker threads, so every endpoint is marshalled onto a queue and
  executed here, once per frame, on the main thread. Without this callback the
  server accepts requests but every one of them times out after 5s — and any
  endpoint that touched an op from the worker thread could wedge TD. Don't skip it.

### 3e. `param_exec` — Parameter Execute DAT (token rotation)

- `Tab` → **Parameter Execute DAT**. Rename to `param_exec`.
- Set its `OP` parameter to `..` (the `TouchAPI` COMP) and turn on the **Pulse** and **Custom** toggles so it fires for the custom `Rotate` pulse.
- Replace the body with:
  ```python
  def onPulse(par):
      mod('td_api_src').rotate_token()
  ```

## 4. Custom parameters on `TouchAPI`

Exit back into `/project1`, select `TouchAPI`, open the **Component Editor** (right-click → `Customize Component...`) and add one page `Settings` with these parameters:

| Name | Type | Default | Notes |
|---|---|---|---|
| `Port` | Int | `44444` | Read-only. Shown for reference; server port is currently hardcoded in `td_api.py`. |
| `Status` | Str | `stopped` | Display-only (set `Enable: Off` on the parameter so users can't edit). Populated by `bootstrap.onStart`. |
| `Rotate` | Pulse | — | Button. Handled by the `param_exec` Parameter Execute DAT (step 3e), which calls `mod('td_api_src').rotate_token()`. |

You can also drive the parameters from a script by selecting `TouchAPI` and running in the textport:
```python
p = op('/project1/TouchAPI').appendCustomPage('Settings')
p.appendInt('Port', label='Port')[0].default = 44444
p.appendInt('Port', label='Port')[0].readOnly = True
p.appendStr('Status', label='Status')[0].enable = False
p.appendPulse('Rotate', label='Rotate token')
```
(Skip the above if you already added the parameters via the Component Editor.)

## 5. Test in place

With the project still open:

1. The `Status` parameter on `TouchAPI` should read `READY @ 127.0.0.1:44444`.
2. In a terminal:
   ```bash
   curl -i http://127.0.0.1:44444/pane
   ```
   Expect `401` (no auth). This confirms the server is bound and the auth gate works.
3. With the token (see [`tests/manual.md`](../tests/manual.md) §5), a GET should return a JSON pane snapshot.

If any of these fail, stop and debug with the textport before saving the `.tox`.

## 6. Save as `.tox`

- Back in `/project1`, right-click the `TouchAPI` COMP → **Save Component .tox...**
- Save as `toe/TouchAPI.tox`, overwriting the previous build.

## 7. Verify round-trip

From the repo root:

```bash
python scripts/extract_tox.py
```

If `toeexpand` is on PATH, this dumps the DATs out of the `.tox` into a sibling folder. Open the extracted `td_api_src.py` and diff against `toe/src/td_api.py`:

```bash
diff toe/TouchAPI.tox.expand/td_api_src.py toe/src/td_api.py
```

**Any drift means the `.tox` and the source disagree** — reject the `.tox`, re-paste `toe/src/td_api.py` into the `td_api_src` DAT, and resave. The `.tox` must be bit-equivalent content to the repo source.

If `toeexpand` is not installed, the script prints manual instructions: open the `.tox` in TD, copy the `td_api_src` DAT text, diff.

---

## Troubleshooting

- **Server doesn't start / Status stays `stopped`.** Open TD's textport (`Alt+T`). Look for Python tracebacks. Almost always: `td_runtime_shim` didn't run before `mod('td_api_src').start_server()`. Manually right-click `td_runtime_shim` → Run Script, then right-click `bootstrap` → Run Script to re-trigger the start hook.
- **Status parameter never updates.** Check that the `bootstrap` Execute DAT's **Active** flag is on. If off, TD ignores all callbacks.
- **Port 44444 already in use.** Another instance of TD (or this server) is already bound. Close the other instance, or in future versions read `parent().par.Port.eval()` and pass it to `start_server(port=...)`. For v0.1 the port is hardcoded in `td_api.py`; the `Port` parameter is informational only.
- **`mod('td_api_src')` raises `NoneType has no attribute ...`.** The Text DAT's **Language** is not set to Python. Set it, then re-trigger `bootstrap.onStart`.
- **Token file isn't generated.** `start_server` creates the directory and file lazily on first token read. If the process lacks write permission to `%APPDATA%\claude-td\`, move the config dir by setting the `CLAUDE_TD_CONFIG_DIR` environment variable before launching TD.
