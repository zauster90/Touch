"""TouchDesigner HTTP API — serves 127.0.0.1 only, token-authenticated.

This file is the authoritative source. It gets copied into a Text DAT inside
`TouchAPI.tox`. Regenerate the .tox after any change and run
`scripts/extract_tox.py` to verify the round-trip.

Stdlib only — runs inside TD 2025's embedded Python (3.11)."""
from __future__ import annotations

import contextlib
import hmac
import io
import json
import os
import secrets
import sys
import tempfile
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
        self.path.write_text(token, encoding="utf-8")
        if sys.platform != "win32":
            os.chmod(self.path, 0o600)


# ---------------------------------------------------------------------------
# Request log
# ---------------------------------------------------------------------------

_log: deque[str] = deque(maxlen=LOG_MAX)


def log(line: str) -> None:
    _log.append(line)


def get_log() -> list[str]:
    return list(_log)


# ---------------------------------------------------------------------------
# Router — endpoints register via @route
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

    def log_message(self, fmt, *args):  # silence stderr; use our log buffer
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


# ---------------------------------------------------------------------------
# Endpoints — read-only
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


# ---------------------------------------------------------------------------
# Endpoints — mutation & complex
# ---------------------------------------------------------------------------

@route("GET", "/params")
def _params_get(ctx: Ctx):
    td = _td()
    path = ctx.q("path")
    if not path:
        return 400, {"error": {"type": "BadInput", "message": "path required"}}
    o = td.op(path)
    if o is None:
        return 404, {"error": {"type": "NotFound", "message": path}}
    pars: dict[str, Any] = {}
    try:
        names = [n for n in dir(o.par) if not n.startswith("_")]
    except Exception:  # noqa: BLE001
        names = []
    for name in names:
        try:
            val = getattr(o.par, name)
            pars[name] = val.eval if hasattr(val, "eval") else val
        except Exception:  # noqa: BLE001
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
    applied: dict[str, Any] = {}
    for k, v in updates.items():
        par = getattr(o.par, k, None)
        try:
            if par is not None and hasattr(par, "val"):
                par.val = v
            else:
                # Create-or-set via attribute — _FakeParGroup and TD both support this.
                setattr(o.par, k, v)
            applied[k] = v
        except Exception as exc:  # noqa: BLE001
            applied[k] = {"error": str(exc)}
    return 200, {"path": path, "applied": applied}


@route("POST", "/create")
def _create(ctx: Ctx):
    td = _td()
    data = ctx.json() or {}
    type_ = data.get("type")
    parent = data.get("parent", "/")
    name = data.get("name")
    pos = data.get("pos")
    inputs = data.get("inputs") or []
    if not type_:
        return 400, {"error": {"type": "BadInput", "message": "type required"}}
    p = td.op(parent)
    if p is None:
        return 404, {"error": {"type": "NotFound", "message": parent}}
    if not hasattr(p, "create"):
        return 500, {"error": {"type": "CreateFailed", "message": "op has no create()"}}
    child = p.create(type_, name=name)
    if pos and hasattr(child, "nodeX"):
        try:
            child.nodeX, child.nodeY = int(pos[0]), int(pos[1])
        except Exception:  # noqa: BLE001
            pass
    for i, src in enumerate(inputs):
        s = td.op(src)
        if s is None:
            continue
        try:
            child.inputConnectors[i].connect(s.outputConnectors[0])
        except Exception:  # noqa: BLE001
            pass
    return 200, _op_info(child)


@route("POST", "/layout")
def _layout(ctx: Ctx):
    import td_layout  # local import so engine errors don't brick the server on boot.
    td = _td()
    data = ctx.json() or {}
    path = data.get("path")
    if not path:
        return 400, {"error": {"type": "BadInput", "message": "path required"}}
    parent = td.op(path)
    if parent is None:
        return 404, {"error": {"type": "NotFound", "message": path}}

    selection_only = bool(data.get("selection_only", False))
    direction = data.get("direction", "LR")
    spacing = data.get("spacing") or {}
    apply_changes = bool(data.get("apply", False))

    # Collect nodes + wires.
    children = parent.findChildren(depth=1)
    if selection_only:
        children = [c for c in children if getattr(c, "selected", False)]
    child_by_path = {c.path: c for c in children}

    nodes = [
        {"id": c.path, "is_feedback_top": getattr(c, "type", "") == "feedbackTOP"}
        for c in children
    ]
    edges: list[dict] = []
    for c in children:
        for ic in getattr(c, "inputConnectors", []) or []:
            for conn in getattr(ic, "connections", []) or []:
                owner = getattr(conn, "owner", None)
                owner_path = getattr(owner, "path", None) if owner is not None else None
                if owner_path and owner_path in child_by_path:
                    edges.append({"from": owner_path, "to": c.path})

    result = td_layout.layout({
        "nodes": nodes, "edges": edges,
        "direction": direction, "spacing": spacing,
    })

    if "error" in result:
        return 400, result

    plan = []
    for cp, (x, y) in result["positions"].items():
        child = child_by_path.get(cp)
        from_xy = (getattr(child, "nodeX", 0), getattr(child, "nodeY", 0)) if child else (0, 0)
        plan.append({"path": cp, "from": list(from_xy), "to": [int(x), int(y)]})

    if apply_changes:
        for cp, (x, y) in result["positions"].items():
            c = child_by_path.get(cp)
            if c is not None and hasattr(c, "nodeX"):
                c.nodeX, c.nodeY = int(x), int(y)

    return 200, {
        "mode": "applied" if apply_changes else "preview",
        "target": path,
        "plan": plan,
        "broken_edges": result["broken_edges"],
        "stats": result["stats"],
    }


@route("GET", "/graph")
def _graph(ctx: Ctx):
    td = _td()
    path = ctx.q("path", "/") or "/"
    try:
        depth = int(ctx.q("depth", "2") or "2")
    except ValueError:
        return 400, {"error": {"type": "BadInput", "message": "depth must be integer"}}
    root = td.op(path)
    if root is None:
        return 404, {"error": {"type": "NotFound", "message": path}}
    nodes: list[dict] = []

    def walk(n, d):
        inputs: list[str] = []
        for ic in getattr(n, "inputConnectors", []) or []:
            for conn in getattr(ic, "connections", []) or []:
                owner = getattr(conn, "owner", None)
                if owner is not None:
                    owner_path = getattr(owner, "path", None)
                    if owner_path is not None:
                        inputs.append(owner_path)
        nodes.append({**_op_info(n), "inputs": inputs})
        if d <= 0:
            return
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
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp = f.name
    try:
        o.save(tmp)
        data = Path(tmp).read_bytes()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return 200, ("image/png", data)
