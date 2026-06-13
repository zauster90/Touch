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


# ---------------------------------------------------------------------------
# Endpoints — data readback (CHOP / DAT) and performance
# ---------------------------------------------------------------------------

@route("GET", "/chop")
def _chop(ctx: Ctx):
    """Sample channel data off a CHOP. Optionally filter by `chan` name pattern
    and cap returned samples per channel with `samples` (default 16, max 600)."""
    td = _td()
    path = ctx.q("path")
    if not path:
        return 400, {"error": {"type": "BadInput", "message": "path required"}}
    o = td.op(path)
    if o is None:
        return 404, {"error": {"type": "NotFound", "message": path}}
    if not hasattr(o, "chans"):
        return 400, {"error": {"type": "BadOp", "message": "not a CHOP (no .chans)"}}
    try:
        max_samples = int(ctx.q("samples", "16") or "16")
    except ValueError:
        return 400, {"error": {"type": "BadInput", "message": "samples must be integer"}}
    max_samples = max(1, min(max_samples, 600))
    pattern = ctx.q("chan")
    chans = o.chans(pattern) if pattern else o.chans()
    out: list[dict] = []
    for c in chans:
        vals = list(getattr(c, "vals", []) or [])
        total = len(vals)
        truncated = total > max_samples
        if truncated:
            # Even stride downsample so the shape survives the cap.
            step = total / max_samples
            vals = [vals[int(i * step)] for i in range(max_samples)]
        out.append({
            "name": getattr(c, "name", None),
            "numSamples": total,
            "truncated": truncated,
            "samples": vals,
        })
    return 200, {"path": path, "numChans": len(out), "channels": out}


@route("GET", "/dat")
def _dat(ctx: Ctx):
    """Read a DAT's table cells (capped by `rows`/`cols`) plus its raw `text`."""
    td = _td()
    path = ctx.q("path")
    if not path:
        return 400, {"error": {"type": "BadInput", "message": "path required"}}
    o = td.op(path)
    if o is None:
        return 404, {"error": {"type": "NotFound", "message": path}}
    if not hasattr(o, "numRows"):
        return 400, {"error": {"type": "BadOp", "message": "not a DAT (no .numRows)"}}
    try:
        max_rows = int(ctx.q("rows", "50") or "50")
        max_cols = int(ctx.q("cols", "20") or "20")
    except ValueError:
        return 400, {"error": {"type": "BadInput", "message": "rows/cols must be integer"}}
    nrows, ncols = o.numRows, o.numCols
    rows: list[list[str]] = []
    for r in range(min(nrows, max(0, max_rows))):
        row: list[str] = []
        for c in range(min(ncols, max(0, max_cols))):
            cell = o[r, c]
            row.append(cell.val if hasattr(cell, "val") else ("" if cell is None else str(cell)))
        rows.append(row)
    return 200, {
        "path": path,
        "numRows": nrows,
        "numCols": ncols,
        "truncated": nrows > max_rows or ncols > max_cols,
        "rows": rows,
        "text": getattr(o, "text", None),
    }


@route("GET", "/perf")
def _perf(ctx: Ctx):
    """Slowest-cooking operators under `path`, descending by last cook time (ms)."""
    td = _td()
    path = ctx.q("path", "/") or "/"
    root = td.op(path)
    if root is None:
        return 404, {"error": {"type": "NotFound", "message": path}}
    try:
        depth = int(ctx.q("depth", "10") or "10")
        top_n = int(ctx.q("top", "10") or "10")
    except ValueError:
        return 400, {"error": {"type": "BadInput", "message": "depth/top must be integer"}}
    rows: list[dict] = []
    for n in [root, *root.findChildren(depth=depth)]:
        ct = getattr(n, "cookTime", None)
        if ct is None:
            continue
        rows.append({**_op_info(n), "cookTime": ct, "totalCooks": getattr(n, "totalCooks", None)})
    rows.sort(key=lambda r: r["cookTime"] or 0, reverse=True)
    return 200, {"path": path, "depth": depth, "count": len(rows), "slowest": rows[:max(1, top_n)]}


# ---------------------------------------------------------------------------
# Endpoints — wiring & lifecycle
# ---------------------------------------------------------------------------

@route("POST", "/connect")
def _connect(ctx: Ctx):
    """Wire `from` op's output into `to` op's input. Indices default to 0."""
    td = _td()
    data = ctx.json() or {}
    src, dst = data.get("from"), data.get("to")
    if not src or not dst:
        return 400, {"error": {"type": "BadInput", "message": "from and to required"}}
    try:
        out_idx = int(data.get("outputIndex", 0) or 0)
        in_idx = int(data.get("inputIndex", 0) or 0)
    except (TypeError, ValueError):
        return 400, {"error": {"type": "BadInput", "message": "indices must be integers"}}
    s, d = td.op(src), td.op(dst)
    if s is None:
        return 404, {"error": {"type": "NotFound", "message": src}}
    if d is None:
        return 404, {"error": {"type": "NotFound", "message": dst}}
    try:
        d.inputConnectors[in_idx].connect(s.outputConnectors[out_idx])
    except Exception as exc:  # noqa: BLE001
        return 200, {"success": False, "error": {"type": type(exc).__name__, "message": str(exc)}}
    return 200, {"success": True, "from": src, "to": dst, "inputIndex": in_idx, "outputIndex": out_idx}


@route("POST", "/disconnect")
def _disconnect(ctx: Ctx):
    """Drop wires into `to`. Omit `inputIndex` to clear all inputs."""
    td = _td()
    data = ctx.json() or {}
    dst = data.get("to")
    if not dst:
        return 400, {"error": {"type": "BadInput", "message": "to required"}}
    d = td.op(dst)
    if d is None:
        return 404, {"error": {"type": "NotFound", "message": dst}}
    in_idx = data.get("inputIndex")
    try:
        if in_idx is None:
            for ic in d.inputConnectors:
                ic.disconnect()
        else:
            d.inputConnectors[int(in_idx)].disconnect()
    except Exception as exc:  # noqa: BLE001
        return 200, {"success": False, "error": {"type": type(exc).__name__, "message": str(exc)}}
    return 200, {"success": True, "to": dst, "inputIndex": in_idx}


@route("POST", "/delete")
def _delete(ctx: Ctx):
    """Destroy the operator at `path`."""
    td = _td()
    data = ctx.json() or {}
    path = data.get("path")
    if not path:
        return 400, {"error": {"type": "BadInput", "message": "path required"}}
    o = td.op(path)
    if o is None:
        return 404, {"error": {"type": "NotFound", "message": path}}
    if not hasattr(o, "destroy"):
        return 400, {"error": {"type": "BadOp", "message": "op has no destroy()"}}
    info = _op_info(o)
    o.destroy()
    return 200, {"success": True, "deleted": info}


@route("POST", "/bind")
def _bind(ctx: Ctx):
    """Bind a parameter reactively. `mode` = "expression" (default) sets
    `param.expr` (e.g. "op('audio')['rms']"); "constant" sets a fixed `val`.
    ParMode is bridged through td_runtime; if absent the value/expr is still
    written and the mode left untouched."""
    td = _td()
    data = ctx.json() or {}
    path, param = data.get("path"), data.get("param")
    if not path or not param:
        return 400, {"error": {"type": "BadInput", "message": "path and param required"}}
    o = td.op(path)
    if o is None:
        return 404, {"error": {"type": "NotFound", "message": path}}
    par = getattr(o.par, param, None)
    if par is None:
        return 404, {"error": {"type": "NotFound", "message": f"{path}.par.{param}"}}
    mode = (data.get("mode") or "expression").lower()
    par_mode = getattr(td, "ParMode", None)
    expr = data.get("expr")
    try:
        if mode == "constant":
            if "val" in data:
                par.val = data["val"]
            if par_mode is not None:
                par.mode = par_mode.CONSTANT
        elif mode == "expression":
            if expr is None:
                return 400, {"error": {"type": "BadInput", "message": "expr required for expression mode"}}
            par.expr = expr
            if par_mode is not None:
                par.mode = par_mode.EXPRESSION
        else:
            return 400, {"error": {"type": "BadInput", "message": f"unknown mode: {mode}"}}
    except Exception as exc:  # noqa: BLE001
        return 200, {"success": False, "error": {"type": type(exc).__name__, "message": str(exc)}}
    return 200, {"success": True, "path": path, "param": param, "mode": mode, "expr": expr}
