# Touch Plugin Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a Claude Code plugin (`touch`) that exposes 9 MCP tools for driving TouchDesigner from Claude, backed by a locally-bound, token-authenticated HTTP server inside TD.

**Architecture:** Flat single-plugin repo. Node/TypeScript MCP server (`src/index.ts`) calls a Python stdlib HTTP server embedded in a TD component (`toe/src/td_api.py`, packaged into `TouchAPI.tox`). All traffic is `127.0.0.1` + bearer token + host check.

**Tech Stack:** TypeScript + `@modelcontextprotocol/sdk` + `zod`, esbuild bundler, Node 20+. Python 3.11 stdlib only (matches TD 2025's embedded Python). Vitest for TS tests. `unittest` for Python tests.

**Working tree:** `C:/Users/zachm/Touch` (already cloned, `main`).

**Design doc:** `docs/plans/2026-04-16-touch-plugin-design.md` (committed).

---

## Conventions for every task

- **TDD:** write the failing test first, watch it fail, implement the minimum to pass, watch it pass, then commit.
- **Commits per task:** one commit at the end of each task, conventional-commits style.
- **Style:** TS uses ESM, `strict: true`, no `any`. Python uses stdlib only (no pip installs), type hints where obvious, no f-strings in logs that contain user input.
- **Do not write `os`/`subprocess` calls in the Python API** except where the handler genuinely needs them (and never on user-controlled strings).
- **Never leak a traceback** over HTTP. Always serialize to `{error: {type, message}}`.
- **Parallel-safe:** tasks marked `[PARALLEL]` do not touch the same files as any preceding un-completed task; they can be dispatched concurrently by an orchestrator.

---

## Task 1: Repo scaffolding

**Files:**
- Create: `C:/Users/zachm/Touch/package.json`
- Create: `C:/Users/zachm/Touch/tsconfig.json`
- Create: `C:/Users/zachm/Touch/.gitignore`
- Create: `C:/Users/zachm/Touch/.claude-plugin/plugin.json`
- Create: `C:/Users/zachm/Touch/.mcp.json`
- Create: `C:/Users/zachm/Touch/src/` (empty dir placeholder via a `.gitkeep` — not needed if we immediately create `index.ts`, skip)
- Create: `C:/Users/zachm/Touch/tests/`
- Create: `C:/Users/zachm/Touch/toe/src/`
- Create: `C:/Users/zachm/Touch/scripts/`

**Step 1: Write `package.json`**

```json
{
  "name": "touch",
  "version": "0.1.0",
  "private": true,
  "description": "Claude Code plugin for TouchDesigner — MCP tools for networks, params, rendering, and shaders.",
  "type": "module",
  "scripts": {
    "build": "esbuild src/index.ts --bundle --platform=node --format=esm --outfile=dist/index.js --banner:js=\"#!/usr/bin/env node\"",
    "dev": "npm run build -- --watch",
    "test": "vitest run",
    "test:watch": "vitest",
    "smoke": "node scripts/smoke.mjs",
    "clean": "rm -rf dist node_modules"
  },
  "dependencies": {
    "@modelcontextprotocol/sdk": "^1.0.0",
    "zod": "^3.24.0"
  },
  "devDependencies": {
    "@types/node": "^20.0.0",
    "esbuild": "^0.24.0",
    "typescript": "^5.5.0",
    "vitest": "^2.0.0"
  },
  "engines": { "node": ">=20" }
}
```

**Step 2: Write `tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "resolveJsonModule": true,
    "types": ["node"],
    "outDir": "dist",
    "rootDir": "src"
  },
  "include": ["src/**/*"]
}
```

**Step 3: Write `.gitignore`**

```
node_modules/
# dist/ is intentionally committed for install-without-build UX
.DS_Store
*.log
# TD junk
*.toe.*.tmp
*.tox.bak
# token/logs only exist at runtime
# (they live outside the repo under %APPDATA%/claude-td)
```

**Step 4: Write `.claude-plugin/plugin.json`**

```json
{
  "name": "touch",
  "description": "TouchDesigner integration for Claude Code — execute Python, query editor state, render, and introspect networks.",
  "version": "0.1.0",
  "author": { "name": "zauster90" }
}
```

Note: no `hooks`, no `commands`, no `allowedTools` block — the plugin is inert until Claude invokes an MCP tool.

**Step 5: Write `.mcp.json`**

```json
{
  "mcpServers": {
    "touch": {
      "command": "node",
      "args": ["${CLAUDE_PLUGIN_ROOT}/dist/index.js"]
    }
  }
}
```

**Step 6: Install deps and verify**

Run (from `C:/Users/zachm/Touch`):
```
npm install
```
Expected: creates `node_modules/`, `package-lock.json`. No errors.

**Step 7: Commit**

```
git add package.json package-lock.json tsconfig.json .gitignore .claude-plugin/plugin.json .mcp.json
git commit -m "chore: scaffold plugin — package, tsconfig, .claude-plugin, .mcp.json"
```

---

## Task 2: Python HTTP server core (token + auth + host + routing shell)

**Files:**
- Create: `C:/Users/zachm/Touch/toe/src/td_api.py`
- Create: `C:/Users/zachm/Touch/tests/python_api_test.py`

**Step 1: Write the failing test `tests/python_api_test.py`**

This test file must start a real `HTTPServer` on an ephemeral port and issue requests via `urllib.request`. No `requests` dependency.

```python
"""Integration tests for the TD-side HTTP API.
Runs without TouchDesigner by injecting a stub `td_runtime` module into td_api."""
import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
from pathlib import Path

# Inject a stub TD runtime BEFORE importing td_api.
_stub = types.ModuleType("td_runtime")

class _FakeOp:
    def __init__(self, path="/", name="root", type_="root", family="COMP"):
        self.path = path
        self.name = name
        self.type = type_
        self.family = family
        self._children = []
        self._pars = {}
    def findChildren(self, depth=1):
        return list(self._children)
    def errors(self): return ""
    def warnings(self): return ""

_stub.op = lambda path=None: _FakeOp(path or "/")
_stub.ui = types.SimpleNamespace(panes=types.SimpleNamespace(current=types.SimpleNamespace(
    owner=_FakeOp("/"), x=0, y=0, zoom=1.0)))
_stub.ops = lambda *args: []
sys.modules["td_runtime"] = _stub

# Add toe/src to path so we can import td_api.
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "toe" / "src"))
import td_api  # noqa: E402


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_TD_CONFIG_DIR"] = cls.tmp.name
        cls.server = td_api.start_server(host="127.0.0.1", port=0)
        cls.port = cls.server.server_address[1]
        cls.token = td_api.TokenManager().load()
        # Give the thread a beat.
        time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()
        os.environ.pop("CLAUDE_TD_CONFIG_DIR", None)

    def _req(self, path, *, token=None, host="localhost", method="GET", body=None, headers=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url, method=method)
        req.add_header("Host", host)
        if token is not None:
            req.add_header("Authorization", f"Bearer {token}")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        data = body.encode() if isinstance(body, str) else body
        try:
            resp = urllib.request.urlopen(req, data=data, timeout=2)
            return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    # --- core middleware ---
    def test_missing_token_returns_401(self):
        status, _ = self._req("/pane")
        self.assertEqual(status, 401)

    def test_wrong_token_returns_401(self):
        status, _ = self._req("/pane", token="nope")
        self.assertEqual(status, 401)

    def test_bad_host_returns_403(self):
        status, _ = self._req("/pane", token=self.token, host="evil.example")
        self.assertEqual(status, 403)

    def test_unknown_route_returns_404(self):
        status, _ = self._req("/not-a-route", token=self.token)
        self.assertEqual(status, 404)

    def test_no_cors_header(self):
        status, _ = self._req("/pane", token=self.token)
        # urlopen doesn't easily expose headers on success w/o inspecting; re-issue via raw conn.
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        conn.request("GET", "/pane", headers={
            "Host": "localhost",
            "Authorization": f"Bearer {self.token}",
        })
        resp = conn.getresponse()
        self.assertIsNone(resp.getheader("Access-Control-Allow-Origin"))
        conn.close()


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run the test and watch it fail**

Run: `python tests/python_api_test.py`
Expected: `ImportError: No module named 'td_api'` (file doesn't exist yet).

**Step 3: Write `toe/src/td_api.py`**

```python
"""TouchDesigner HTTP API — serves 127.0.0.1 only, token-authenticated.

This file is the authoritative source. It gets copied into a Text DAT inside
`TouchAPI.tox`. Regenerate the .tox after any change and run
`scripts/extract_tox.py` to verify the round-trip.

Stdlib only — runs inside TD 2025's embedded Python (3.11)."""
from __future__ import annotations

import base64
import hmac
import json
import os
import secrets
import sys
import threading
import traceback
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

# TD runtime is injected (by the tox bootstrap) or stubbed (in tests).
# We import lazily so test harnesses can inject a fake `td_runtime` module first.
def _td():
    import td_runtime  # type: ignore
    return td_runtime


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_PORT = 44444
ALLOWED_HOSTS = {"localhost", "127.0.0.1"}
LOG_MAX = 500


def _config_dir() -> Path:
    # Tests override via env var; otherwise use platform default.
    override = os.environ.get("CLAUDE_TD_CONFIG_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "claude-td"
    return Path.home() / ".config" / "claude-td"


# ---------------------------------------------------------------------------
# Token manager
# ---------------------------------------------------------------------------

class TokenManager:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (_config_dir() / "token")

    def load(self) -> str:
        if not self.path.exists():
            self._generate()
        return self.path.read_text(encoding="utf-8").strip()

    def rotate(self) -> str:
        self._generate()
        return self.load()

    def _generate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(32)
        # write and then chmod 0600 on POSIX
        self.path.write_text(token, encoding="utf-8")
        if sys.platform != "win32":
            os.chmod(self.path, 0o600)


# ---------------------------------------------------------------------------
# Request log
# ---------------------------------------------------------------------------

_log: deque[str] = deque(maxlen=LOG_MAX)

def log(line: str) -> None:
    _log.append(line)


# ---------------------------------------------------------------------------
# Router — endpoints register here
# ---------------------------------------------------------------------------

Endpoint = Callable[["Ctx"], tuple[int, Any]]
_routes: dict[tuple[str, str], Endpoint] = {}


def route(method: str, path: str):
    def deco(fn: Endpoint) -> Endpoint:
        _routes[(method, path)] = fn
        return fn
    return deco


class Ctx:
    """Per-request context passed to endpoints."""
    def __init__(self, handler: "TDHandler", query: dict[str, list[str]], body: bytes):
        self.handler = handler
        self.query = query
        self.body = body

    def q(self, key: str, default: str | None = None) -> str | None:
        vals = self.query.get(key)
        return vals[0] if vals else default

    def json(self) -> Any:
        if not self.body:
            return None
        return json.loads(self.body.decode("utf-8"))


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

class TDHandler(BaseHTTPRequestHandler):
    # Populated in start_server
    token: str = ""

    def log_message(self, fmt, *args):  # silence stderr spam; use our log buffer
        log(f"{self.address_string()} - {fmt % args}")

    # ---- middleware ------------------------------------------------------
    def _host_ok(self) -> bool:
        raw = self.headers.get("Host", "")
        host = raw.split(":", 1)[0].lower()
        return host in ALLOWED_HOSTS

    def _auth_ok(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return False
        provided = auth[len("Bearer "):].strip()
        return hmac.compare_digest(provided, self.token)

    def _deny(self, code: int, reason: str) -> None:
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()
        log(f"DENY {code} {reason} {self.path}")

    def _respond(self, status: int, body: Any, *, content_type: str = "application/json") -> None:
        if isinstance(body, (bytes, bytearray)):
            payload = bytes(body)
        else:
            payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        # NOTE: never send Access-Control-Allow-Origin.
        self.end_headers()
        self.wfile.write(payload)

    # ---- dispatch --------------------------------------------------------
    def _handle(self, method: str) -> None:
        if not self._host_ok():
            return self._deny(403, "bad_host")
        if not self._auth_ok():
            return self._deny(401, "bad_token")

        parsed = urlparse(self.path)
        key = (method, parsed.path)
        endpoint = _routes.get(key)
        if endpoint is None:
            return self._deny(404, "no_route")

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""

        ctx = Ctx(self, parse_qs(parsed.query), body)
        try:
            status, payload = endpoint(ctx)
        except Exception as exc:  # noqa: BLE001
            log(f"ERR {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
            return self._respond(500, {"error": {"type": type(exc).__name__, "message": str(exc)}})

        if isinstance(payload, tuple) and len(payload) == 2 and isinstance(payload[1], (bytes, bytearray)):
            # (content_type, bytes) form — for binary responses like PNG.
            content_type, data = payload
            return self._respond(status, data, content_type=content_type)

        self._respond(status, payload)

    def do_GET(self): self._handle("GET")
    def do_POST(self): self._handle("POST")
    def do_PATCH(self): self._handle("PATCH")


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

_server: HTTPServer | None = None
_server_thread: threading.Thread | None = None


def start_server(host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> HTTPServer:
    global _server, _server_thread
    assert host == "127.0.0.1", "Only 127.0.0.1 is permitted"
    token = TokenManager().load()
    TDHandler.token = token
    _server = HTTPServer((host, port), TDHandler)
    _server_thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _server_thread.start()
    log(f"START on {host}:{_server.server_address[1]}")
    return _server


def stop_server() -> None:
    global _server, _server_thread
    if _server is not None:
        _server.shutdown()
        _server.server_close()
    _server = None
    _server_thread = None


def rotate_token() -> str:
    new = TokenManager().rotate()
    TDHandler.token = new
    log("TOKEN rotated")
    return new
```

**Step 4: Run the test and verify it passes**

Run: `python tests/python_api_test.py`
Expected: 5 tests pass (missing token 401, wrong token 401, bad host 403, unknown route 404, no CORS header).

**Step 5: Commit**

```
git add toe/src/td_api.py tests/python_api_test.py
git commit -m "feat(py): HTTP server core with token auth, host check, logging"
```

---

## Task 3: Python read-only endpoints (`/pane`, `/selection`, `/operators`, `/errors`)

**Files:**
- Modify: `C:/Users/zachm/Touch/toe/src/td_api.py`
- Modify: `C:/Users/zachm/Touch/tests/python_api_test.py`

**Step 1: Extend the test stub in `tests/python_api_test.py`**

Add more to the stub `op` tree: a `geo1` child with `findChildren` returning two SOPs, and `ui.panes.current.owner` returning the root with `.path == "/project1"`.

Add tests:

```python
    def test_pane_returns_state(self):
        status, body = self._req("/pane", token=self.token)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn("networkPath", data)
        self.assertIn("zoom", data)

    def test_selection_returns_list(self):
        status, body = self._req("/selection", token=self.token)
        self.assertEqual(status, 200)
        self.assertIsInstance(json.loads(body)["operators"], list)

    def test_operators_at_root(self):
        status, body = self._req("/operators?path=/", token=self.token)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["path"], "/")
        self.assertIsInstance(data["operators"], list)

    def test_errors_returns_structure(self):
        status, body = self._req("/errors", token=self.token)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn("errors", data)
        self.assertIn("warnings", data)
```

**Step 2: Run tests — watch them fail with 404**

**Step 3: Implement in `td_api.py`** (append below the handler class):

```python
# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def _op_info(o) -> dict:
    return {
        "path": getattr(o, "path", None),
        "name": getattr(o, "name", None),
        "type": getattr(o, "type", None),
        "family": getattr(o, "family", None),
    }


@route("GET", "/pane")
def _pane(ctx: Ctx):
    td = _td()
    pane = td.ui.panes.current
    owner = pane.owner
    return 200, {
        "networkPath": getattr(owner, "path", "/"),
        "x": getattr(pane, "x", 0),
        "y": getattr(pane, "y", 0),
        "zoom": getattr(pane, "zoom", 1.0),
    }


@route("GET", "/selection")
def _selection(ctx: Ctx):
    td = _td()
    sel = getattr(td.ui, "selected", None)
    if callable(sel):
        ops = sel()
    else:
        # fallback: scan current pane owner for selected children
        owner = td.ui.panes.current.owner
        ops = [c for c in owner.findChildren(depth=1) if getattr(c, "selected", False)]
    return 200, {"operators": [_op_info(o) for o in ops]}


@route("GET", "/operators")
def _operators(ctx: Ctx):
    td = _td()
    path = ctx.q("path", "/") or "/"
    parent = td.op(path)
    if parent is None:
        return 404, {"error": {"type": "NotFound", "message": f"op not found: {path}"}}
    kids = parent.findChildren(depth=1)
    return 200, {"path": path, "operators": [_op_info(k) for k in kids]}


@route("GET", "/errors")
def _errors(ctx: Ctx):
    td = _td()
    root = td.op("/")
    if root is None:
        return 500, {"error": {"type": "NoRoot", "message": "cannot access op('/')"}}
    errs: list[dict] = []
    warns: list[dict] = []
    for n in [root, *root.findChildren(depth=10)]:
        e = n.errors() or ""
        w = n.warnings() or ""
        if e: errs.append({"path": n.path, "text": e})
        if w: warns.append({"path": n.path, "text": w})
    return 200, {"errors": errs, "warnings": warns}
```

**Step 4: Run tests — pass**

**Step 5: Commit**

```
git add toe/src/td_api.py tests/python_api_test.py
git commit -m "feat(py): read-only endpoints — pane, selection, operators, errors"
```

---

## Task 4: Python `/execute` endpoint

**Files:**
- Modify: `toe/src/td_api.py`
- Modify: `tests/python_api_test.py`

**Step 1: Tests**

```python
    def test_execute_stdout(self):
        status, body = self._req(
            "/execute", token=self.token, method="POST",
            body="print('hi')")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["success"])
        self.assertIn("hi", data["stdout"])

    def test_execute_exception_returns_200_with_error(self):
        status, body = self._req(
            "/execute", token=self.token, method="POST",
            body="raise ValueError('boom')")
        self.assertEqual(status, 200)   # transport OK; code error surfaced in body
        data = json.loads(body)
        self.assertFalse(data["success"])
        self.assertEqual(data["error"]["type"], "ValueError")
```

**Step 2: Run — fail.**

**Step 3: Implement in `td_api.py`**

```python
import contextlib
import io


@route("POST", "/execute")
def _execute(ctx: Ctx):
    td = _td()
    code = ctx.body.decode("utf-8")
    from_op = ctx.q("from_op", "/") or "/"
    me = td.op(from_op)
    stdout, stderr = io.StringIO(), io.StringIO()
    scope = {"op": td.op, "ops": getattr(td, "ops", None), "ui": td.ui, "me": me}
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exec(compile(code, "<td_execute>", "exec"), scope)  # noqa: S102
        return 200, {
            "success": True,
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
            "from_op": from_op,
        }
    except Exception as exc:  # noqa: BLE001
        return 200, {
            "success": False,
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
            "from_op": from_op,
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }
```

Also add `import io, contextlib` at top of file (keep imports tidy).

**Step 4: Run — pass.**

**Step 5: Commit**

```
git add toe/src/td_api.py tests/python_api_test.py
git commit -m "feat(py): /execute endpoint with captured stdout/stderr"
```

---

## Task 5: Python mutation & complex endpoints (`/params`, `/create`, `/graph`, `/screenshot`)

**Files:**
- Modify: `toe/src/td_api.py`
- Modify: `tests/python_api_test.py`

**Step 1: Extend stubs in `python_api_test.py`**

Stub `op()` returns need:
- `.par` with `.tx`, `.ty` attributes and iteration support for read.
- `findChildren(depth=...)` returning a tree.
- `.save(path)` for `TOP.save()` simulation.
- a global `op()` that supports `op("/path").create(...)`? Actually we should stub `parent.create(type_, name=...)` — parent has `.create(typeStr, name=...) -> child`.

```python
class _FakePar:
    def __init__(self, val): self.val = val
    @property
    def eval(self): return self.val

class _FakeOp:
    # extend existing stub
    # ... (add) ...
    def __init__(self, path="/", name="root", type_="root", family="COMP"):
        self.path = path
        self.name = name
        self.type = type_
        self.family = family
        self._children: list["_FakeOp"] = []
        self.par = types.SimpleNamespace(tx=_FakePar(0), ty=_FakePar(0))
    def findChildren(self, depth=1): return list(self._children)
    def errors(self): return ""
    def warnings(self): return ""
    def create(self, type_, name=None):
        child = _FakeOp(path=f"{self.path.rstrip('/')}/{name or type_}", name=name or type_, type_=type_, family="SOP")
        self._children.append(child)
        return child
    def save(self, filepath):
        Path(filepath).write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)  # minimal PNG signature + padding
```

And register the root with a child so `/graph` has something to walk.

Tests to add:

```python
    def test_params_read(self):
        status, body = self._req("/params?path=/foo", token=self.token)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn("params", data)

    def test_params_write(self):
        status, body = self._req(
            "/params?path=/foo", token=self.token, method="PATCH",
            body=json.dumps({"tx": 5}), headers={"Content-Type": "application/json"})
        self.assertEqual(status, 200)

    def test_create_operator(self):
        payload = {"type": "geo", "parent": "/", "name": "geoA", "pos": [100, 200]}
        status, body = self._req("/create", token=self.token, method="POST",
            body=json.dumps(payload), headers={"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["name"], "geoA")

    def test_graph(self):
        status, body = self._req("/graph?path=/&depth=2", token=self.token)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn("nodes", data)

    def test_screenshot_png(self):
        status, body = self._req("/screenshot?path=/", token=self.token)
        self.assertEqual(status, 200)
        # PNG signature
        self.assertEqual(body[:8], b"\x89PNG\r\n\x1a\n")
```

**Step 2: Run — fail.**

**Step 3: Implement in `td_api.py`**

```python
@route("GET", "/params")
def _params_get(ctx: Ctx):
    td = _td()
    path = ctx.q("path")
    if not path:
        return 400, {"error": {"type": "BadInput", "message": "path required"}}
    o = td.op(path)
    if o is None:
        return 404, {"error": {"type": "NotFound", "message": path}}
    # par may be a namespace (tests) or a TD Par collection
    pars = {}
    for name in dir(o.par):
        if name.startswith("_"): continue
        try:
            val = getattr(o.par, name)
            pars[name] = val.eval if hasattr(val, "eval") else val
        except Exception:
            pass
    return 200, {"path": path, "params": pars}


@route("PATCH", "/params")
def _params_set(ctx: Ctx):
    td = _td()
    path = ctx.q("path")
    if not path:
        return 400, {"error": {"type": "BadInput", "message": "path required"}}
    o = td.op(path)
    if o is None:
        return 404, {"error": {"type": "NotFound", "message": path}}
    updates = ctx.json() or {}
    applied = {}
    for k, v in updates.items():
        par = getattr(o.par, k, None)
        if par is None:
            continue
        try:
            # TD Par supports .val = x; our test stub uses _FakePar which exposes .val too.
            if hasattr(par, "val"):
                par.val = v
            else:
                setattr(o.par, k, v)
            applied[k] = v
        except Exception as exc:  # noqa: BLE001
            applied[k] = {"error": str(exc)}
    return 200, {"path": path, "applied": applied}


@route("POST", "/create")
def _create(ctx: Ctx):
    td = _td()
    data = ctx.json() or {}
    type_ = data.get("type"); parent = data.get("parent", "/"); name = data.get("name")
    pos = data.get("pos"); inputs = data.get("inputs") or []
    if not type_:
        return 400, {"error": {"type": "BadInput", "message": "type required"}}
    p = td.op(parent)
    if p is None:
        return 404, {"error": {"type": "NotFound", "message": parent}}
    child = p.create(type_, name=name) if hasattr(p, "create") else None
    if child is None:
        return 500, {"error": {"type": "CreateFailed", "message": "op has no create()"}}
    if pos and hasattr(child, "nodeX"):
        child.nodeX, child.nodeY = int(pos[0]), int(pos[1])
    # wire inputs
    for i, src in enumerate(inputs):
        s = td.op(src)
        if s is None: continue
        try:
            child.inputConnectors[i].connect(s.outputConnectors[0])
        except Exception:
            pass
    return 200, _op_info(child)


@route("GET", "/graph")
def _graph(ctx: Ctx):
    td = _td()
    path = ctx.q("path", "/") or "/"
    depth = int(ctx.q("depth", "2") or "2")
    root = td.op(path)
    if root is None:
        return 404, {"error": {"type": "NotFound", "message": path}}
    nodes = []
    def walk(n, d):
        inputs = []
        for ic in getattr(n, "inputConnectors", []) or []:
            for conn in getattr(ic, "connections", []) or []:
                owner = getattr(conn, "owner", None)
                if owner is not None:
                    inputs.append(getattr(owner, "path", None))
        nodes.append({**_op_info(n), "inputs": inputs})
        if d <= 0: return
        for c in n.findChildren(depth=1):
            walk(c, d - 1)
    walk(root, depth)
    return 200, {"path": path, "depth": depth, "nodes": nodes}


@route("GET", "/screenshot")
def _screenshot(ctx: Ctx):
    td = _td()
    path = ctx.q("path")
    if not path:
        return 400, {"error": {"type": "BadInput", "message": "path required"}}
    o = td.op(path)
    if o is None:
        return 404, {"error": {"type": "NotFound", "message": path}}
    if not hasattr(o, "save"):
        return 400, {"error": {"type": "BadOp", "message": "op has no .save (not a TOP?)"}}
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp = f.name
    try:
        o.save(tmp)
        data = Path(tmp).read_bytes()
    finally:
        try: os.unlink(tmp)
        except OSError: pass
    return 200, ("image/png", data)
```

**Step 4: Run — pass.**

**Step 5: Commit**

```
git add toe/src/td_api.py tests/python_api_test.py
git commit -m "feat(py): params, create, graph, screenshot endpoints"
```

---

## Task 6: TypeScript MCP server + all 9 tools + tests

**Files:**
- Create: `src/index.ts`
- Create: `tests/api.test.ts`
- Create: `vitest.config.ts` (needed to load ESM+TS)

**Step 1: Write `vitest.config.ts`**

```ts
import { defineConfig } from "vitest/config";
export default defineConfig({
  test: { environment: "node", include: ["tests/**/*.test.ts"] },
});
```

**Step 2: Write the failing test `tests/api.test.ts`**

The test starts a mini `http` server in-process, injects its port + a token via env, then imports and drives the MCP tool handlers directly (no need to spawn MCP transport).

```ts
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import http from "node:http";
import { AddressInfo } from "node:net";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

// ---- ephemeral token file ----
let tokenDir: string;
let tokenPath: string;
const TOKEN = "test-token-xyz";

// ---- fake TD HTTP server ----
let server: http.Server;
const calls: Array<{ method: string; url: string; headers: Record<string,string>; body: string }> = [];

beforeAll(async () => {
  tokenDir = fs.mkdtempSync(path.join(os.tmpdir(), "claude-td-"));
  tokenPath = path.join(tokenDir, "token");
  fs.writeFileSync(tokenPath, TOKEN);
  process.env.CLAUDE_TD_TOKEN_PATH = tokenPath;

  server = http.createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      calls.push({
        method: req.method!,
        url: req.url!,
        headers: req.headers as Record<string,string>,
        body: Buffer.concat(chunks).toString("utf8"),
      });
      const url = req.url!;
      if (url.startsWith("/pane"))
        return res.writeHead(200, {"content-type":"application/json"}).end(JSON.stringify({networkPath:"/",x:0,y:0,zoom:1}));
      if (url.startsWith("/selection"))
        return res.writeHead(200, {"content-type":"application/json"}).end(JSON.stringify({operators:[]}));
      if (url.startsWith("/operators"))
        return res.writeHead(200, {"content-type":"application/json"}).end(JSON.stringify({path:"/",operators:[]}));
      if (url.startsWith("/errors"))
        return res.writeHead(200, {"content-type":"application/json"}).end(JSON.stringify({errors:[],warnings:[]}));
      if (url.startsWith("/execute"))
        return res.writeHead(200, {"content-type":"application/json"}).end(JSON.stringify({success:true, stdout:"", stderr:"", from_op:"/"}));
      if (url.startsWith("/params"))
        return res.writeHead(200, {"content-type":"application/json"}).end(JSON.stringify({path:"/foo", params:{tx:0}}));
      if (url.startsWith("/graph"))
        return res.writeHead(200, {"content-type":"application/json"}).end(JSON.stringify({path:"/", depth:2, nodes:[]}));
      if (url.startsWith("/create"))
        return res.writeHead(200, {"content-type":"application/json"}).end(JSON.stringify({path:"/geoA", name:"geoA", type:"geo"}));
      if (url.startsWith("/screenshot"))
        return res.writeHead(200, {"content-type":"image/png"}).end(Buffer.from([0x89,0x50,0x4e,0x47,0x0d,0x0a,0x1a,0x0a]));
      res.writeHead(404).end();
    });
  });
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
  const port = (server.address() as AddressInfo).port;
  process.env.TDAPI_PORT = String(port);
});

afterAll(async () => {
  await new Promise<void>((r) => server.close(() => r()));
  fs.rmSync(tokenDir, { recursive: true, force: true });
});

describe("td() client", () => {
  it("sends bearer token and host: localhost", async () => {
    const { td } = await import("../src/index.js");
    const res = await td("/pane");
    expect(res.networkPath).toBe("/");
    const call = calls.at(-1)!;
    expect(call.headers.authorization).toBe(`Bearer ${TOKEN}`);
    expect(call.headers.host?.startsWith("localhost")).toBe(true);
  });

  it("screenshot returns base64 PNG content", async () => {
    const { screenshotTool } = await import("../src/index.js");
    const out = await screenshotTool({ path: "/foo" });
    expect(out.content[0].type).toBe("image");
    expect(out.content[0].mimeType).toBe("image/png");
  });

  it("404 on bad route throws with message", async () => {
    const { td } = await import("../src/index.js");
    await expect(td("/nope")).rejects.toThrow(/404/);
  });
});
```

**Step 3: Run tests — fail (no `src/index.js`).**

Run: `npm run test`

**Step 4: Implement `src/index.ts`**

```ts
#!/usr/bin/env node
/**
 * Touch — TouchDesigner MCP server.
 *
 * Connects to a Python HTTP server bound to 127.0.0.1 inside TouchDesigner.
 * Every request carries a bearer token sourced from %APPDATA%/claude-td/token
 * (or CLAUDE_TD_TOKEN_PATH for tests).
 */
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { z } from "zod";

// ---------------------------------------------------------------------------
// Config + token
// ---------------------------------------------------------------------------

const PORT = Number(process.env.TDAPI_PORT ?? 44444);
const HOST = "127.0.0.1";

function tokenPath(): string {
  if (process.env.CLAUDE_TD_TOKEN_PATH) return process.env.CLAUDE_TD_TOKEN_PATH;
  if (process.platform === "win32") {
    const base = process.env.APPDATA ?? path.join(os.homedir(), "AppData", "Roaming");
    return path.join(base, "claude-td", "token");
  }
  return path.join(os.homedir(), ".config", "claude-td", "token");
}

function loadToken(): string {
  const p = tokenPath();
  if (!fs.existsSync(p)) {
    throw new Error(
      `Touch token not found at ${p}. Load the TouchAPI.tox into your TD project first.`
    );
  }
  return fs.readFileSync(p, "utf8").trim();
}

let TOKEN: string | null = null;
function getToken(): string {
  if (TOKEN === null) TOKEN = loadToken();
  return TOKEN;
}

// ---------------------------------------------------------------------------
// HTTP helper (exported for tests)
// ---------------------------------------------------------------------------

export async function td(endpoint: string, init: RequestInit = {}): Promise<any> {
  const url = `http://${HOST}:${PORT}${endpoint}`;
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${getToken()}`);
  headers.set("Host", "localhost");
  const res = await fetch(url, { ...init, headers });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}${text ? `: ${text}` : ""}`);
  }
  const ct = res.headers.get("content-type") ?? "";
  if (ct.startsWith("image/")) return { __binary: true, mimeType: ct, buffer: Buffer.from(await res.arrayBuffer()) };
  return res.json();
}

// ---------------------------------------------------------------------------
// Tool handlers (exported for direct test)
// ---------------------------------------------------------------------------

const asText = (obj: unknown) => ({ content: [{ type: "text" as const, text: JSON.stringify(obj, null, 2) }] });

export async function executeTool({ code, from_op }: { code: string; from_op?: string }) {
  const q = from_op ? `?from_op=${encodeURIComponent(from_op)}` : "";
  return asText(await td(`/execute${q}`, { method: "POST", body: code, headers: { "Content-Type": "text/plain" } }));
}
export async function paneTool() { return asText(await td("/pane")); }
export async function selectionTool() { return asText(await td("/selection")); }
export async function operatorsTool({ path: p }: { path?: string }) {
  return asText(await td(`/operators?path=${encodeURIComponent(p ?? "/")}`));
}
export async function paramsTool({ path: p, params }: { path: string; params?: Record<string, unknown> }) {
  const q = `?path=${encodeURIComponent(p)}`;
  if (params !== undefined) {
    return asText(await td(`/params${q}`, {
      method: "PATCH", body: JSON.stringify(params),
      headers: { "Content-Type": "application/json" },
    }));
  }
  return asText(await td(`/params${q}`));
}
export async function errorsTool() { return asText(await td("/errors")); }
export async function screenshotTool({ path: p }: { path: string; width?: number; height?: number }) {
  const q = `?path=${encodeURIComponent(p)}`;
  const r = await td(`/screenshot${q}`);
  if (!r?.__binary) return asText(r);
  return { content: [{ type: "image" as const, data: r.buffer.toString("base64"), mimeType: r.mimeType }] };
}
export async function graphTool({ path: p, depth }: { path?: string; depth?: number }) {
  const q = `?path=${encodeURIComponent(p ?? "/")}&depth=${depth ?? 2}`;
  return asText(await td(`/graph${q}`));
}
export async function createTool(args: { type: string; parent: string; name?: string; pos?: [number, number]; inputs?: string[] }) {
  return asText(await td("/create", {
    method: "POST", body: JSON.stringify(args),
    headers: { "Content-Type": "application/json" },
  }));
}

// ---------------------------------------------------------------------------
// MCP server wiring
// ---------------------------------------------------------------------------

function buildServer(): McpServer {
  const server = new McpServer({ name: "touch", version: "0.1.0" });

  server.registerTool("td_execute", {
    title: "Execute Python in TouchDesigner",
    description: "Run Python code inside TD. `me` refers to the from_op context operator.",
    inputSchema: { code: z.string(), from_op: z.string().optional() },
  }, executeTool);

  server.registerTool("td_pane", {
    title: "Get network editor pane state",
    description: "Current pane: networkPath, x, y, zoom.",
    inputSchema: {},
  }, paneTool);

  server.registerTool("td_selection", {
    title: "Get selected operators",
    description: "Operators currently selected in the active pane.",
    inputSchema: {},
  }, selectionTool);

  server.registerTool("td_operators", {
    title: "List operators at a path",
    description: "List direct children of the operator at `path` (default '/').",
    inputSchema: { path: z.string().optional() },
  }, operatorsTool);

  server.registerTool("td_params", {
    title: "Read or write parameters",
    description: "Read params (omit `params`) or patch them (`{ tx: 5 }` style).",
    inputSchema: { path: z.string(), params: z.record(z.any()).optional() },
  }, paramsTool);

  server.registerTool("td_errors", {
    title: "Get project errors and warnings",
    description: "Walks the project tree and returns any TD errors/warnings.",
    inputSchema: {},
  }, errorsTool);

  server.registerTool("td_screenshot", {
    title: "Screenshot a TOP",
    description: "Returns a PNG of a TOP's current frame for visual inspection.",
    inputSchema: { path: z.string(), width: z.number().optional(), height: z.number().optional() },
  }, screenshotTool);

  server.registerTool("td_graph", {
    title: "Export a subgraph as JSON",
    description: "Structured graph of a subnet: nodes + implied wires via `inputs`.",
    inputSchema: { path: z.string().optional(), depth: z.number().optional() },
  }, graphTool);

  server.registerTool("td_create", {
    title: "Create an operator",
    description: "Create an operator of `type` under `parent`; optional `name`, `pos`, and `inputs` (paths to wire).",
    inputSchema: {
      type: z.string(),
      parent: z.string(),
      name: z.string().optional(),
      pos: z.tuple([z.number(), z.number()]).optional(),
      inputs: z.array(z.string()).optional(),
    },
  }, createTool);

  return server;
}

async function main() {
  const server = buildServer();
  await server.connect(new StdioServerTransport());
}

// Only auto-run when executed as a script, not when imported by tests.
const isMain = import.meta.url.endsWith(process.argv[1]?.replace(/\\/g, "/") ?? "");
if (isMain) {
  main().catch((e) => { console.error(e); process.exit(1); });
}
```

**Step 5: Run TS tests — pass**

Run: `npm run test`
Expected: 3 tests pass.

**Step 6: Build**

Run: `npm run build`
Expected: `dist/index.js` created.

**Step 7: Commit**

```
git add src/index.ts tests/api.test.ts vitest.config.ts dist/
git commit -m "feat(ts): MCP server + 9 tools + client unit tests"
```

---

## Task 7 [PARALLEL-SAFE]: Utility scripts (`scripts/smoke.mjs`, `scripts/extract_tox.py`)

**Files:**
- Create: `scripts/smoke.mjs`
- Create: `scripts/extract_tox.py`

**Step 1: Write `scripts/smoke.mjs`**

```js
#!/usr/bin/env node
/** Calls every MCP tool once against a running TD. Run: `npm run smoke`. */
import * as api from "../dist/index.js";

const checks = [
  ["td_pane", () => api.paneTool()],
  ["td_selection", () => api.selectionTool()],
  ["td_operators", () => api.operatorsTool({ path: "/" })],
  ["td_errors", () => api.errorsTool()],
  ["td_params (read)", () => api.paramsTool({ path: "/" })],
  ["td_graph", () => api.graphTool({ path: "/", depth: 1 })],
  ["td_execute", () => api.executeTool({ code: "print('smoke ok')" })],
];

let failed = 0;
for (const [name, fn] of checks) {
  try {
    const out = await fn();
    const txt = out.content?.[0]?.text ?? "<non-text>";
    console.log(`✓ ${name} — ${txt.slice(0, 80).replace(/\n/g, " ")}`);
  } catch (e) {
    console.error(`✗ ${name} — ${e.message}`);
    failed++;
  }
}
process.exit(failed ? 1 : 0);
```

**Step 2: Write `scripts/extract_tox.py`**

```python
"""Extract Python DAT text from TouchAPI.tox and write it to toe/src/td_api.py.

Usage: python scripts/extract_tox.py  (requires TouchDesigner installed)

The .tox format is a TD binary. TD ships a `toeexpand` tool that unpacks tox/toe
into readable XML. If that's not available, this script falls back to invoking
TouchDesigner in -cmd mode to dump the DAT text.
"""
from __future__ import annotations
import subprocess, sys, os, shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOX = REPO / "toe" / "TouchAPI.tox"
OUT = REPO / "toe" / "src" / "td_api.py"

def main() -> int:
    if not TOX.exists():
        print(f"no tox at {TOX}; nothing to extract")
        return 0
    # Prefer toeexpand if present.
    toeexpand = shutil.which("toeexpand")
    if toeexpand:
        subprocess.run([toeexpand, str(TOX)], check=True)
        # Users inspect the expanded folder and copy td_api DAT text over OUT.
        print(f"Expanded {TOX}. Copy the td_api DAT text to {OUT} and diff.")
        return 0
    print("toeexpand not in PATH. Open the .tox in TD and copy the td_api Text DAT "
          "contents into toe/src/td_api.py manually, then diff.")
    return 1

if __name__ == "__main__":
    sys.exit(main())
```

**Step 3: Commit**

```
git add scripts/
git commit -m "chore: add smoke + tox-extract helper scripts"
```

---

## Task 8 [PARALLEL-SAFE]: `td-guide` skill

**Files:**
- Create: `skills/td-guide/SKILL.md`
- Create: `skills/td-guide/reference/operators.md`
- Create: `skills/td-guide/reference/rendering.md`
- Create: `skills/td-guide/reference/glsl.md`
- Create: `skills/td-guide/reference/instancing.md`
- Create: `skills/td-guide/reference/feedback.md`
- Create: `skills/td-guide/reference/tools.md`

**Content guidance:** Each reference file is ~50–120 lines of terse, opinionated notes — not an exhaustive TD manual. Emphasis on what Claude needs to know to make good choices (names of common operators per family, which TOP resolves "make it look like X", how GLSL I/O differs in TOP vs POP, common feedback loop pitfalls, when to reach for `td_execute` vs `td_create`/`td_params`).

`SKILL.md` header:

```markdown
---
name: td-guide
description: Use when building or modifying TouchDesigner networks — covers operator families, rendering, GLSL, instancing, feedback, and which MCP tool to reach for.
---

# td-guide

Quick index:
- [operators](reference/operators.md) — SOP/POP/TOP/CHOP/DAT/COMP families, common ops
- [rendering](reference/rendering.md) — Camera / Light / Render TOP / Geometry COMP
- [glsl](reference/glsl.md) — GLSL TOP, GLSL MAT, POP GLSL quirks
- [instancing](reference/instancing.md) — Geometry COMP instancing from CHOP/DAT/SOP
- [feedback](reference/feedback.md) — feedback TOP loops, frame delay, reset strategy
- [tools](reference/tools.md) — when to use td_execute vs td_create vs td_params

Default to the highest-level MCP tool that does the job; fall back to td_execute
only when no specific tool fits.
```

Write the six reference files with ~500 lines total. Bias toward concise cheat-sheet prose, not tutorials.

**Commit:**
```
git add skills/
git commit -m "docs(skill): td-guide skill with 6 reference files"
```

---

## Task 9 [PARALLEL-SAFE]: README, manual test doc, BUILD_TOX guide

**Files:**
- Create: `README.md`
- Create: `tests/manual.md`
- Create: `toe/BUILD_TOX.md`

**`README.md`** — cover: what this is, security posture (three bullets: 127.0.0.1, bearer, host check), install flow (`/plugin install C:/Users/zachm/Touch`), loading the `.tox`, list of 9 tools, how to rotate the token, how to regenerate the `.tox` after editing `td_api.py`, license (MIT), pointer to `docs/plans/`.

**`tests/manual.md`** — numbered smoke-test checklist the user runs with a real TD up:
1. Open TD 2025+ with a fresh project.
2. Drag `toe/TouchAPI.tox` into `/project1`.
3. Confirm the component's Status custom parameter reads "READY @ 127.0.0.1:44444".
4. In another terminal: `npm run smoke` — expect 7 ✓ rows.
5. In Claude Code: `/plugin install C:/Users/zachm/Touch` then `"ping touchdesigner"` — Claude should call `td_pane` and report the path.
6. Exercise each tool (table with "say this / expect that" rows).
7. Press the `Rotate token` button; confirm next Claude call errors clearly.

**`toe/BUILD_TOX.md`** — step-by-step for rebuilding `TouchAPI.tox` from source:
1. Open `toe/develop.toe` (or a blank project).
2. Add a Container COMP named `TouchAPI`.
3. Inside it: Text DAT `td_api_src` — paste the contents of `toe/src/td_api.py` and set Language: Python.
4. Execute DAT `bootstrap` with `onStart`/`onExit`/`onProjectPreSave` hooks — content provided in this doc (short snippet that injects `td_runtime` and calls `td_api_src.module.start_server()`).
5. Custom parameters on `TouchAPI`: `Port` (Int, default 44444, read-only), `Status` (Str, display-only), `Rotate` (Pulse button triggering `td_api_src.module.rotate_token()`).
6. Save As → `toe/TouchAPI.tox`.
7. Run `python scripts/extract_tox.py` and diff against `toe/src/td_api.py` to verify round-trip.

**Commit:**
```
git add README.md tests/manual.md toe/BUILD_TOX.md
git commit -m "docs: README, manual test checklist, tox build guide"
```

---

## Task 10: Final verification, push, handoff

**Step 1: Full verification**

```
npm run test
npm run build
python tests/python_api_test.py
```

All three must pass with zero failures.

**Step 2: Status check**

```
git status                   # clean
git log --oneline -20        # ~9 commits
```

**Step 3: Push**

```
git push origin main
```

**Step 4: Hand off to user**

Print the manual steps they need to do:
1. Open TD 2025+, follow `toe/BUILD_TOX.md` to create `TouchAPI.tox`.
2. Commit & push the `.tox`.
3. Run the `tests/manual.md` checklist.
4. `/plugin install C:/Users/zachm/Touch` in Claude Code.

---

## Parallelization hints (for subagent-driven execution)

- Tasks 1 → 2 → 3 → 4 → 5 → 6 → 7 are a linear chain (each modifies the previous task's files or builds on its tests).
- Tasks 7, 8, 9 can run in parallel after Task 6 completes — they touch disjoint files.
- Task 10 must come last.
- Each agent should: read the design doc + this plan + the preceding relevant files, run the task's TDD loop end-to-end, and commit on completion. An orchestrator invokes the next task's agent after the previous commit lands.
