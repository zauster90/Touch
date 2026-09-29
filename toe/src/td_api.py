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
import queue
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
# Main-thread marshaling
# ---------------------------------------------------------------------------
# TD's Python API (op(), ui.*, par access) is only safe on the main cook
# thread. The HTTP server runs handlers on worker threads, so we queue work
# here and drain it from an Execute DAT's onFrameStart callback.

_main_q: queue.Queue = queue.Queue()


def run_on_main(fn: Callable[[], Any], timeout: float = 5.0) -> Any:
    ev = threading.Event()
    box: list = [None, None]  # [result, exception]

    def wrapped():
        try:
            box[0] = fn()
        except BaseException as e:  # noqa: BLE001
            box[1] = e
        finally:
            ev.set()

    _main_q.put(wrapped)
    if not ev.wait(timeout=timeout):
        raise TimeoutError(f"main-thread call timed out after {timeout}s")
    if box[1] is not None:
        raise box[1]
    return box[0]


def drain_main_queue() -> int:
    """Call from TD main thread every frame. Returns tasks executed."""
    n = 0
    while True:
        try:
            fn = _main_q.get_nowait()
        except queue.Empty:
            return n
        try:
            fn()
        except Exception:
            log(f"main-thread task error:\n{traceback.format_exc()}")
        n += 1


# ---------------------------------------------------------------------------
# Router — endpoints register via @route
# ---------------------------------------------------------------------------

Endpoint = Callable[["Ctx"], tuple[int, Any]]
_routes: dict[tuple[str, str], Endpoint] = {}


DEFAULT_TIMEOUT = 5.0


def route(method: str, path: str, timeout: float = DEFAULT_TIMEOUT):
    """Register an endpoint. `timeout` bounds how long the HTTP worker waits
    for the main thread to run it (long for /execute, short elsewhere)."""
    def deco(fn: Endpoint) -> Endpoint:
        fn.timeout = timeout  # type: ignore[attr-defined]
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

    # JSON serialization fallback. Runs on the HTTP worker thread — must NOT
    # access TD OP attributes from here (TD detects cross-thread access and
    # pops a modal that blocks the main cook thread, wedging the whole app).
    # The endpoint is responsible for coercing any OPs to plain str/int/etc.
    # on the main thread before returning. If an OP slips through anyway,
    # return a safe placeholder instead of dereffing it.
    @staticmethod
    def _json_coerce(o):
        # type().__name__ is always safe — no TD OP attribute access.
        return f"<unserializable:{type(o).__name__}>"

    def _respond(self, status: int, body: Any, *, content_type: str = "application/json") -> None:
        if isinstance(body, (bytes, bytearray)):
            payload = bytes(body)
        else:
            payload = json.dumps(body, default=self._json_coerce).encode("utf-8")
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
            # Marshal endpoint execution onto TD's main cook thread.
            # TD Python API (op(), ui.*, par access) is not thread-safe.
            status, payload = run_on_main(
                lambda: endpoint(ctx), timeout=getattr(endpoint, "timeout", DEFAULT_TIMEOUT))
        except TimeoutError as exc:
            # The request is still queued and will run on the next frame; we
            # just stop waiting. Usually means TD is blocked (modal dialog,
            # long cook) or the bootstrap's onFrameStart drain isn't firing.
            log(f"TIMEOUT {self.path}: {exc}")
            return self._respond(504, {"error": {"type": "Timeout", "message": (
                f"{exc}. TD's main thread did not pick up the request — check for "
                "an open dialog, a very long cook, or that TouchAPI/bootstrap is active.")}})
        except Exception as exc:  # noqa: BLE001
            log(f"ERR {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
            return self._respond(500, {"error": {"type": type(exc).__name__, "message": str(exc)}})

        if isinstance(payload, tuple) and len(payload) == 2 and isinstance(payload[1], (bytes, bytearray)):
            # (content_type, bytes) form — for binary responses like PNG.
            content_type, data = payload
            try:
                return self._respond(status, data, content_type=content_type)
            except Exception as exc:  # noqa: BLE001
                log(f"ERR respond-binary {type(exc).__name__}: {exc}")
                return self._respond(500, {"error": {"type": type(exc).__name__, "message": str(exc)}})

        # Guard the final JSON response too — if it throws (e.g. un-serializable
        # object slipped through _json_coerce), fall back to a 500 rather than
        # letting the exception kill the single-threaded HTTP worker.
        try:
            self._respond(status, payload)
        except Exception as exc:  # noqa: BLE001
            log(f"ERR respond-json {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
            try:
                self._respond(500, {"error": {"type": type(exc).__name__, "message": str(exc)}})
            except Exception:  # noqa: BLE001
                pass

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


def _err(status: int, type_: str, message: str) -> tuple[int, dict]:
    return status, {"error": {"type": type_, "message": message}}


def _tdg(name: str, default: Any = None) -> Any:
    """Look up a TD global (families, project, app, noiseTOP, ...). Prefers the
    td_runtime shim, then TD's own `td` module; `default` when neither has it."""
    val = getattr(_td(), name, None)
    if val is not None:
        return val
    try:
        import td as _tdmod  # type: ignore  # only importable inside TD
    except ImportError:
        return default
    return getattr(_tdmod, name, default)


def _is_op(v: Any) -> bool:
    return v is not None and hasattr(v, "path") and hasattr(v, "family")


def _jsonable(v: Any, _depth: int = 0) -> Any:
    """Coerce a value to plain JSON on the MAIN thread. OPs become their path;
    containers recurse; anything else unknown becomes its repr."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if _is_op(v):
        return v.path
    if _depth > 4:
        return repr(v)
    if isinstance(v, dict):
        return {str(k): _jsonable(x, _depth + 1) for k, x in list(v.items())[:500]}
    if isinstance(v, (list, tuple, set, deque)):
        return [_jsonable(x, _depth + 1) for x in list(v)[:500]]
    name = getattr(v, "name", None)
    if isinstance(name, str) and type(v).__name__.endswith("Mode"):
        return name  # enum-ish, e.g. ParMode.EXPRESSION -> "EXPRESSION"
    try:
        return repr(v)
    except Exception:  # noqa: BLE001
        return f"<{type(v).__name__}>"


def _int_q(ctx: "Ctx", key: str, default: int) -> int:
    raw = ctx.q(key)
    return default if raw in (None, "") else int(raw)


def _connections(o) -> tuple[list[dict], list[dict]]:
    """(inputs, outputs) wiring of an op as [{index, path}] lists."""
    ins: list[dict] = []
    for i, ic in enumerate(getattr(o, "inputConnectors", []) or []):
        for conn in getattr(ic, "connections", []) or []:
            owner = getattr(conn, "owner", None)
            if owner is not None:
                ins.append({"index": i, "path": getattr(owner, "path", None)})
    outs: list[dict] = []
    for i, oc in enumerate(getattr(o, "outputConnectors", []) or []):
        for conn in getattr(oc, "connections", []) or []:
            owner = getattr(conn, "owner", None)
            if owner is not None and owner is not o:
                outs.append({"index": i, "path": getattr(owner, "path", None)})
    return ins, outs


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
    """Errors + warnings for every op under `path` (default '/'), `depth` deep."""
    td = _td()
    path = ctx.q("path", "/") or "/"
    root = td.op(path)
    if root is None:
        return _err(404, "NotFound", path)
    try:
        depth = _int_q(ctx, "depth", 64)
    except ValueError:
        return _err(400, "BadInput", "depth must be integer")
    errs: list[dict] = []
    warns: list[dict] = []
    kids = root.findChildren(depth=depth) if hasattr(root, "findChildren") else []
    for n in [root, *kids]:
        e = n.errors() or ""
        w = n.warnings() or ""
        if e: errs.append({"path": n.path, "text": e})
        if w: warns.append({"path": n.path, "text": w})
    return 200, {"path": path, "errors": errs, "warnings": warns}


def _exec_scope(td, me) -> dict:
    """Globals for /execute: everything TD's own textport has (op, parent,
    absTime, noiseTOP, tdu, ...) when running inside TD, plus `me`."""
    scope: dict[str, Any] = {}
    try:
        import td as _tdmod  # type: ignore  # only importable inside TD
        scope.update({k: v for k, v in vars(_tdmod).items() if not k.startswith("__")})
    except ImportError:
        pass
    scope.update({"op": td.op, "ops": getattr(td, "ops", None), "ui": td.ui, "me": me})
    if me is not None and hasattr(me, "parent"):
        scope["parent"] = me.parent
    scope["__name__"] = "__td_execute__"
    return scope


def _split_last_expr(code: str):
    """Compile `code` so a trailing expression's value can be returned, like a
    REPL: (body_code, last_expr_code_or_None)."""
    import ast
    tree = ast.parse(code, "<td_execute>", "exec")
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last = ast.Expression(tree.body.pop().value)
        return compile(tree, "<td_execute>", "exec"), compile(last, "<td_execute>", "eval")
    return compile(tree, "<td_execute>", "exec"), None


@route("POST", "/execute", timeout=30.0)
def _execute(ctx: Ctx):
    td = _td()
    code = ctx.body.decode("utf-8")
    from_op = ctx.q("from_op", "/") or "/"
    me = td.op(from_op)
    stdout, stderr = io.StringIO(), io.StringIO()
    scope = _exec_scope(td, me)
    base = {"stdout": "", "stderr": "", "from_op": from_op}
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            body, last = _split_last_expr(code)
            exec(body, scope)  # noqa: S102
            result = eval(last, scope) if last is not None else None  # noqa: S307
        return 200, {
            **base, "success": True,
            "stdout": stdout.getvalue(), "stderr": stderr.getvalue(),
            "result": _jsonable(result),
        }
    except Exception as exc:  # noqa: BLE001
        # Keep only frames from the submitted code — TD/server frames are noise.
        tb = [ln for ln in traceback.format_exception(exc) if "<td_execute>" in ln or not ln.startswith("  File")]
        line = getattr(exc, "lineno", None)
        if line is None:
            frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == "<td_execute>"]
            line = frames[-1].lineno if frames else None
        return 200, {
            **base, "success": False,
            "stdout": stdout.getvalue(), "stderr": stderr.getvalue(),
            "error": {"type": type(exc).__name__, "message": str(exc), "line": line,
                      "traceback": "".join(tb)[-4000:]},
        }


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
# Endpoints — mutation & complex
# ---------------------------------------------------------------------------

def _mode_name(par) -> str:
    m = getattr(par, "mode", None)
    if m is None:
        return "CONSTANT"
    return getattr(m, "name", None) or str(m).rsplit(".", 1)[-1]


def _par_detail(par, value: Any) -> dict:
    d: dict[str, Any] = {"val": value, "mode": _mode_name(par)}
    for attr in ("expr", "bindExpr", "default", "label", "style", "normMin", "normMax",
                 "min", "max", "clampMin", "clampMax", "menuNames", "readOnly", "enable"):
        try:
            v = getattr(par, attr)
        except Exception:  # noqa: BLE001
            continue
        if v in (None, "") or callable(v):
            continue
        d[attr] = _jsonable(v)
    page = getattr(par, "page", None)
    if page is not None:
        d["page"] = getattr(page, "name", None)
    return d


def _is_default(par, value: Any) -> bool:
    if _mode_name(par) != "CONSTANT":
        return False
    try:
        return value == _jsonable(par.default)
    except Exception:  # noqa: BLE001
        return False


@route("GET", "/params")
def _params_get(ctx: Ctx):
    """Parameter values. Filters: `names` (comma-separated globs), `nondefault=1`
    (only params changed from default or driven by an expression/export).
    `detail=1` returns mode/expr/default/range/menu info per param."""
    import fnmatch
    td = _td()
    path = ctx.q("path")
    if not path:
        return _err(400, "BadInput", "path required")
    o = td.op(path)
    if o is None:
        return _err(404, "NotFound", path)
    patterns = [n.strip() for n in (ctx.q("names") or "").split(",") if n.strip()]
    detail = ctx.q("detail") in ("1", "true")
    nondefault = ctx.q("nondefault") in ("1", "true")
    # Use `o.pars()` — the canonical TD API for enumerating a node's parameters.
    # `dir(o.par)` returns pseudo-attributes like `owner` (raw OP reference) that
    # leak into the response and trigger TD's cross-thread safety dialog when
    # JSON serialization tries to read `.path` from the HTTP worker thread.
    # All OP coercion MUST happen on THIS (main) thread before returning.
    pars: dict[str, Any] = {}
    try:
        par_list = list(o.pars())
    except Exception:  # noqa: BLE001
        par_list = []
    for par in par_list:
        if patterns and not any(fnmatch.fnmatchcase(par.name, pat) for pat in patterns):
            continue
        try:
            # OP-typed pars return an OP object; _jsonable turns it into .path.
            value = _jsonable(par.eval())
        except Exception:  # noqa: BLE001
            continue
        if nondefault and _is_default(par, value):
            continue
        pars[par.name] = _par_detail(par, value) if detail else value
    return 200, {"path": path, "params": pars}


def _set_par(o, name: str, v: Any, td) -> Any:
    """Apply one PATCH entry. Plain values set the constant; `{"expr": ...}`
    switches to expression mode; truthy on a pulse par fires it."""
    par = getattr(o.par, name, None)
    if par is None:
        raise KeyError(f"no parameter '{name}' on {getattr(o, 'path', '?')}")
    par_mode = getattr(td, "ParMode", None)
    if isinstance(v, dict) and "expr" in v:
        par.expr = v["expr"]
        if par_mode is not None:
            par.mode = par_mode.EXPRESSION
        return {"expr": v["expr"]}
    if getattr(par, "isPulse", False) and hasattr(par, "pulse"):
        if v:
            par.pulse()
        return "pulsed" if v else "skipped"
    par.val = v
    if par_mode is not None and _mode_name(par) not in ("CONSTANT", "BIND"):
        par.mode = par_mode.CONSTANT  # a literal value means "stop following the expr"
    return v


@route("PATCH", "/params")
def _params_set(ctx: Ctx):
    td = _td()
    path = ctx.q("path")
    if not path:
        return _err(400, "BadInput", "path required")
    o = td.op(path)
    if o is None:
        return _err(404, "NotFound", path)
    updates = ctx.json() or {}
    applied: dict[str, Any] = {}
    failed: dict[str, str] = {}
    for k, v in updates.items():
        try:
            applied[k] = _set_par(o, k, v, td)
        except Exception as exc:  # noqa: BLE001
            failed[k] = str(exc)
    body: dict[str, Any] = {"path": path, "applied": applied}
    if failed:
        body["failed"] = failed
    return 200, body


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


def _resolve_type(type_: str) -> Any:
    """'noiseTOP' -> the noiseTOP class when running in TD, else the string."""
    cls = _tdg(type_) if isinstance(type_, str) else type_
    return cls if cls is not None else type_


def _all_types() -> dict[str, list[str]]:
    """{family: [type names]} from TD's `families` global, or by scanning the
    td module for *TOP / *CHOP / ... classes as a fallback."""
    fams = _tdg("families")
    out: dict[str, list[str]] = {}
    if isinstance(fams, dict):
        for fam, types in fams.items():
            out[str(fam)] = sorted(getattr(t, "__name__", str(t)) for t in types)
        return out
    try:
        import td as _tdmod  # type: ignore
    except ImportError:
        return out
    for fam in ("TOP", "CHOP", "SOP", "DAT", "COMP", "MAT", "POP"):
        out[fam] = sorted(n for n, v in vars(_tdmod).items()
                          if n.endswith(fam) and n != fam and isinstance(v, type))
    return out


@route("POST", "/create")
def _create(ctx: Ctx):
    td = _td()
    data = ctx.json() or {}
    type_ = data.get("type")
    parent = data.get("parent", "/")
    name = data.get("name")
    pos = data.get("pos")
    inputs = data.get("inputs") or []
    params = data.get("params") or {}
    if not type_:
        return _err(400, "BadInput", "type required")
    p = td.op(parent)
    if p is None:
        return _err(404, "NotFound", parent)
    if not hasattr(p, "create"):
        return _err(400, "BadOp", f"{parent} is not a COMP (no create())")
    if name and hasattr(p, "op") and p.op(name) is not None:
        return _err(409, "NameTaken", f"{parent}/{name} already exists")
    try:
        child = p.create(_resolve_type(type_), name) if name else p.create(_resolve_type(type_))
    except Exception as exc:  # noqa: BLE001
        import difflib
        names = [n for ns in _all_types().values() for n in ns]
        hint = difflib.get_close_matches(str(type_), names, n=5, cutoff=0.5)
        msg = f"cannot create '{type_}': {exc}"
        if hint:
            msg += f". Did you mean: {', '.join(hint)}?"
        return _err(400, "CreateFailed", msg)

    warnings: list[str] = []
    wired: list[str] = []
    for i, src in enumerate(inputs):
        s = td.op(src)
        if s is None:
            warnings.append(f"input {i}: op not found: {src}")
            continue
        try:
            child.inputConnectors[i].connect(s.outputConnectors[0])
            wired.append(s.path)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"input {i} ({src}): {exc}")

    if hasattr(child, "nodeX"):
        try:
            if pos:
                child.nodeX, child.nodeY = int(pos[0]), int(pos[1])
            elif wired:
                # Drop it just downstream of its first input instead of at the origin.
                first = td.op(wired[0])
                child.nodeX, child.nodeY = int(first.nodeX) + 200, int(first.nodeY)
        except Exception:  # noqa: BLE001
            pass

    applied: dict[str, Any] = {}
    for k, v in params.items():
        try:
            applied[k] = _set_par(child, k, v, td)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"param {k}: {exc}")

    body: dict[str, Any] = {**_op_info(child), "inputs": wired}
    if applied:
        body["params"] = applied
    if warnings:
        body["warnings"] = warnings
    return 200, body


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
    rank_gap = data.get("rank_gap")
    node_gap = data.get("node_gap")
    # exclude: list of child names OR full paths to omit from layout. Curated
    # subnets (plugins, vendor COMPs) that the caller doesn't want the layout
    # engine to reposition OR link to via edges.
    exclude_raw = data.get("exclude") or []
    exclude_names = {e for e in exclude_raw if isinstance(e, str)}

    # Collect nodes + wires, filtering out excluded children before building
    # either node list or edge list — excluded ops keep their current position.
    children_all = parent.findChildren(depth=1)
    if selection_only:
        children_all = [c for c in children_all if getattr(c, "selected", False)]
    def _is_excluded(c):
        return c.name in exclude_names or c.path in exclude_names
    children = [c for c in children_all if not _is_excluded(c)]
    excluded = [c.path for c in children_all if _is_excluded(c)]
    child_by_path = {c.path: c for c in children}

    nodes = [
        {"id": c.path, "is_feedback_top": getattr(c, "type", "") == "feedbackTOP"}
        for c in children
    ]
    # Size map for size-aware placement — TD's node-editor dimensions in pixels.
    sizes = {
        c.path: (int(getattr(c, "nodeWidth", 0) or 0),
                 int(getattr(c, "nodeHeight", 0) or 0))
        for c in children
    }
    edges: list[dict] = []
    for c in children:
        for ic in getattr(c, "inputConnectors", []) or []:
            for conn in getattr(ic, "connections", []) or []:
                owner = getattr(conn, "owner", None)
                owner_path = getattr(owner, "path", None) if owner is not None else None
                if owner_path and owner_path in child_by_path:
                    edges.append({"from": owner_path, "to": c.path})

    layout_input: dict = {
        "nodes": nodes, "edges": edges, "sizes": sizes,
        "direction": direction, "spacing": spacing,
    }
    if rank_gap is not None:
        layout_input["rank_gap"] = int(rank_gap)
    if node_gap is not None:
        layout_input["node_gap"] = int(node_gap)
    result = td_layout.layout(layout_input)

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
        "overlaps": result.get("overlaps", []),
        "excluded": excluded,
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
    """A TOP's current frame. `format=jpg` is ~5-10x smaller than png — use it
    when you only need to eyeball the result."""
    td = _td()
    path = ctx.q("path")
    if not path:
        return _err(400, "BadInput", "path required")
    o = td.op(path)
    if o is None:
        return _err(404, "NotFound", path)
    if not hasattr(o, "save"):
        return _err(400, "BadOp", "op has no .save (not a TOP?)")
    fmt = (ctx.q("format", "png") or "png").lower()
    if fmt not in ("png", "jpg", "jpeg"):
        return _err(400, "BadInput", "format must be png or jpg")
    ext, mime = (".png", "image/png") if fmt == "png" else (".jpg", "image/jpeg")
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
        tmp = f.name
    try:
        o.save(tmp)
        data = Path(tmp).read_bytes()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return 200, (mime, data)


# ---------------------------------------------------------------------------
# Endpoints — inspection & discovery
# ---------------------------------------------------------------------------

# Family-specific attributes worth surfacing in /info. Read with getattr, so
# anything missing on a given TD build is simply skipped.
_FAMILY_ATTRS: dict[str, tuple[str, ...]] = {
    "TOP": ("width", "height", "aspect", "depth"),
    "CHOP": ("numChans", "numSamples", "rate", "start", "end", "isTimeSlice"),
    "SOP": ("numPoints", "numPrims", "numVertices"),
    "POP": ("numPoints", "numPrims"),
    "DAT": ("numRows", "numCols", "isTable", "isText"),
    "COMP": (),
    "MAT": (),
}
_FLAG_ATTRS = ("bypass", "display", "render", "lock", "viewer", "allowCooking", "cloneImmune")


def _safe(o, attr: str) -> Any:
    try:
        v = getattr(o, attr)
        return None if callable(v) else _jsonable(v)
    except Exception:  # noqa: BLE001
        return None


@route("GET", "/info")
def _info(ctx: Ctx):
    """Everything useful about ONE op in a single call: wiring both ways, flags,
    errors, cook stats, and family-specific facts (TOP resolution, CHOP channel
    names, SOP point counts, DAT size, COMP children)."""
    td = _td()
    path = ctx.q("path")
    if not path:
        return _err(400, "BadInput", "path required")
    o = td.op(path)
    if o is None:
        return _err(404, "NotFound", path)
    ins, outs = _connections(o)
    info: dict[str, Any] = {**_op_info(o), "inputs": ins, "outputs": outs}
    parent = o.parent() if callable(getattr(o, "parent", None)) else None
    info["parent"] = getattr(parent, "path", None)
    info["flags"] = {a: v for a in _FLAG_ATTRS if (v := _safe(o, a)) is not None}
    info["node"] = {a: v for a in ("nodeX", "nodeY", "nodeWidth", "nodeHeight", "color", "comment")
                    if (v := _safe(o, a)) not in (None, "")}
    info["cook"] = {a: v for a in ("cookTime", "cpuCookTime", "gpuCookTime", "totalCooks", "cookFrame")
                    if (v := _safe(o, a)) is not None}
    errs = o.errors() if hasattr(o, "errors") else ""
    warns = o.warnings() if hasattr(o, "warnings") else ""
    if errs: info["errors"] = errs
    if warns: info["warnings"] = warns
    family = getattr(o, "family", "") or ""
    facts = {a: v for a in _FAMILY_ATTRS.get(family, ()) if (v := _safe(o, a)) is not None}
    if family == "CHOP" and hasattr(o, "chans"):
        names = [getattr(c, "name", "?") for c in o.chans()]
        facts["chanNames"] = names[:64]
        if len(names) > 64:
            facts["chanNamesTruncated"] = True
    if family == "COMP":
        try:
            kids = o.findChildren(depth=1)
            facts["numChildren"] = len(kids)
        except Exception:  # noqa: BLE001
            pass
        pages = getattr(o, "customPages", None)
        if pages:
            facts["customPages"] = {
                getattr(pg, "name", "?"): [getattr(pr, "name", "?") for pr in getattr(pg, "pars", [])]
                for pg in pages
            }
    if facts:
        info["facts"] = facts
    return 200, info


@route("GET", "/find")
def _find(ctx: Ctx):
    """Search recursively under `path` by `name` / `type` glob, `family`, and
    `errors=1` (only ops with errors or warnings). Returns at most `limit`."""
    import fnmatch
    td = _td()
    path = ctx.q("path", "/") or "/"
    root = td.op(path)
    if root is None:
        return _err(404, "NotFound", path)
    if not hasattr(root, "findChildren"):
        return _err(400, "BadOp", f"{path} is not a COMP — search from its parent")
    try:
        depth = _int_q(ctx, "depth", 64)
        limit = max(1, min(_int_q(ctx, "limit", 100), 2000))
    except ValueError:
        return _err(400, "BadInput", "depth/limit must be integer")
    name_pat, type_pat = ctx.q("name"), ctx.q("type")
    family = (ctx.q("family") or "").upper()
    only_errors = ctx.q("errors") in ("1", "true")
    hits: list[dict] = []
    total = 0
    for n in root.findChildren(depth=depth):
        if name_pat and not fnmatch.fnmatch(n.name, name_pat):
            continue
        if type_pat and not fnmatch.fnmatch(str(getattr(n, "type", "")), type_pat):
            continue
        if family and str(getattr(n, "family", "")).upper() != family:
            continue
        if only_errors and not ((n.errors() or "") or (n.warnings() or "")):
            continue
        total += 1
        if len(hits) < limit:
            hits.append(_op_info(n))
    return 200, {"path": path, "count": total, "truncated": total > limit, "operators": hits}


@route("GET", "/types")
def _types(ctx: Ctx):
    """Creatable operator type names (for td_create), by family, with an
    optional substring `filter` (case-insensitive)."""
    family = (ctx.q("family") or "").upper()
    needle = (ctx.q("filter") or "").lower()
    all_types = _all_types()
    if not all_types:
        return _err(501, "Unavailable", "operator type list not available (rebuild TouchAPI to export `families`)")
    out = {
        fam: [t for t in names if needle in t.lower()]
        for fam, names in all_types.items()
        if not family or fam.upper() == family
    }
    return 200, {"types": {k: v for k, v in out.items() if v}}


# ---------------------------------------------------------------------------
# Endpoints — editing
# ---------------------------------------------------------------------------

@route("POST", "/dat/write")
def _dat_write(ctx: Ctx):
    """Write a DAT: `text` replaces its contents (shaders, scripts, JSON);
    `rows` replaces the table (or appends with `append: true`)."""
    td = _td()
    data = ctx.json() or {}
    path = data.get("path")
    if not path:
        return _err(400, "BadInput", "path required")
    o = td.op(path)
    if o is None:
        return _err(404, "NotFound", path)
    text, rows = data.get("text"), data.get("rows")
    if (text is None) == (rows is None):
        return _err(400, "BadInput", "pass exactly one of text or rows")
    if getattr(o, "family", "DAT") != "DAT":
        return _err(400, "BadOp", f"{path} is a {o.family}, not a DAT")
    try:
        if text is not None:
            o.text = str(text)
        else:
            if not isinstance(rows, list) or not all(isinstance(r, list) for r in rows):
                return _err(400, "BadInput", "rows must be a list of lists")
            if not data.get("append"):
                o.clear()
            for r in rows:
                o.appendRow([_cell(c) for c in r])
    except Exception as exc:  # noqa: BLE001
        return 200, {"success": False, "error": {"type": type(exc).__name__, "message": str(exc)}}
    return 200, {"success": True, "path": path,
                 "numRows": getattr(o, "numRows", None), "numCols": getattr(o, "numCols", None)}


def _cell(v: Any) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    return "" if v is None else str(v)


@route("POST", "/node")
def _node(ctx: Ctx):
    """Edit an op's node-level state: `name` (rename), `pos` [x, y], `color`
    [r, g, b] (0-1), `comment`, and flags `bypass/display/render/lock/viewer`."""
    td = _td()
    data = ctx.json() or {}
    path = data.get("path")
    if not path:
        return _err(400, "BadInput", "path required")
    o = td.op(path)
    if o is None:
        return _err(404, "NotFound", path)
    changed: dict[str, Any] = {}
    failed: dict[str, str] = {}

    def attempt(key: str, fn: Callable[[], Any]) -> None:
        try:
            changed[key] = fn()
        except Exception as exc:  # noqa: BLE001
            failed[key] = str(exc)

    if "name" in data:
        def rename():
            o.name = str(data["name"])
            return o.name
        attempt("name", rename)
    if "pos" in data:
        def move():
            o.nodeX, o.nodeY = int(data["pos"][0]), int(data["pos"][1])
            return [o.nodeX, o.nodeY]
        attempt("pos", move)
    if "color" in data:
        def recolor():
            o.color = tuple(float(c) for c in data["color"][:3])
            return list(o.color)
        attempt("color", recolor)
    if "comment" in data:
        def comment():
            o.comment = str(data["comment"])
            return o.comment
        attempt("comment", comment)
    for flag in ("bypass", "display", "render", "lock", "viewer"):
        if flag in data:
            def set_flag(flag=flag):
                if not hasattr(o, flag):
                    raise AttributeError(f"{o.family} has no '{flag}' flag")
                setattr(o, flag, bool(data[flag]))
                return getattr(o, flag)
            attempt(flag, set_flag)
    body: dict[str, Any] = {"path": getattr(o, "path", path), "changed": changed}
    if failed:
        body["failed"] = failed
    return 200, body


@route("POST", "/copy")
def _copy(ctx: Ctx):
    """Duplicate an op (a COMP copies its whole subnet) into `parent` (default:
    same parent) with optional `name` and `pos`. Wires are not copied."""
    td = _td()
    data = ctx.json() or {}
    path = data.get("path")
    if not path:
        return _err(400, "BadInput", "path required")
    o = td.op(path)
    if o is None:
        return _err(404, "NotFound", path)
    dest = td.op(data["parent"]) if data.get("parent") else o.parent()
    if dest is None:
        return _err(404, "NotFound", data.get("parent") or f"{path} has no parent to copy into")
    if not hasattr(dest, "copy"):
        return _err(400, "BadOp", f"{dest.path} is not a COMP (no copy())")
    name = data.get("name")
    try:
        new = dest.copy(o, name=name) if name else dest.copy(o)
    except Exception as exc:  # noqa: BLE001
        return _err(400, "CopyFailed", str(exc))
    pos = data.get("pos")
    try:
        if pos:
            new.nodeX, new.nodeY = int(pos[0]), int(pos[1])
        elif getattr(dest, "path", None) == getattr(o.parent(), "path", None):
            new.nodeX, new.nodeY = int(o.nodeX), int(o.nodeY) - 150
    except Exception:  # noqa: BLE001
        pass
    return 200, {"success": True, "source": path, "copy": _op_info(new)}


_PAR_STYLES = {
    "float": "Float", "int": "Int", "toggle": "Toggle", "str": "Str", "menu": "Menu",
    "strmenu": "StrMenu", "pulse": "Pulse", "momentary": "Momentary", "rgb": "RGB",
    "rgba": "RGBA", "xy": "XY", "xyz": "XYZ", "xyzw": "XYZW", "uv": "UV", "uvw": "UVW",
    "wh": "WH", "file": "File", "folder": "Folder", "op": "OP", "comp": "COMP",
    "top": "TOP", "chop": "CHOP", "sop": "SOP", "dat": "DAT", "mat": "MAT",
    "python": "Python", "header": "Header",
}


def _custom_par_name(raw: str) -> str:
    """TD requires custom par names to be Capitalized + lowercase/digits."""
    clean = "".join(ch for ch in raw if ch.isalnum())
    if not clean or not clean[0].isalpha():
        raise ValueError(f"invalid custom parameter name: {raw!r}")
    return clean[0].upper() + clean[1:].lower()


@route("POST", "/custom_par")
def _custom_par(ctx: Ctx):
    """Add (or replace) a custom parameter on a COMP — the way to give a
    component a clean control surface. Values apply to every tuplet member."""
    td = _td()
    data = ctx.json() or {}
    path, raw_name = data.get("path"), data.get("name")
    if not path or not raw_name:
        return _err(400, "BadInput", "path and name required")
    o = td.op(path)
    if o is None:
        return _err(404, "NotFound", path)
    if not hasattr(o, "appendCustomPage"):
        return _err(400, "BadOp", f"{path} is not a COMP (custom params live on COMPs)")
    style = _PAR_STYLES.get(str(data.get("style", "float")).lower())
    if style is None:
        return _err(400, "BadInput", f"unknown style; use one of {sorted(set(_PAR_STYLES.values()))}")
    try:
        name = _custom_par_name(raw_name)
    except ValueError as exc:
        return _err(400, "BadInput", str(exc))
    page_name = data.get("page") or "Custom"
    page = next((pg for pg in (getattr(o, "customPages", None) or []) if pg.name == page_name), None)
    if page is None:
        page = o.appendCustomPage(page_name)
    kwargs: dict[str, Any] = {"label": data.get("label") or raw_name, "replace": True}
    if style in ("Float", "Int") and data.get("size"):
        kwargs["size"] = int(data["size"])
    try:
        group = getattr(page, f"append{style}")(name, **kwargs)
    except Exception as exc:  # noqa: BLE001
        return _err(400, "CreateFailed", f"append{style}: {exc}")
    warnings: list[str] = []

    def put(par, attr: str, value: Any) -> None:
        try:
            setattr(par, attr, value)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{par.name}.{attr}: {exc}")

    try:
        members = list(group)  # ParGroup / list of tuplet pars
    except TypeError:
        members = [group]
    for par in members:
        if "min" in data:
            put(par, "normMin", data["min"])
        if "max" in data:
            put(par, "normMax", data["max"])
        if data.get("clamp"):
            for attr, key in (("min", "min"), ("max", "max")):
                if key in data:
                    put(par, attr, data[key])
                    put(par, f"clamp{attr.capitalize()}", True)
        if "menuNames" in data:
            put(par, "menuNames", list(data["menuNames"]))
            put(par, "menuLabels", list(data.get("menuLabels") or data["menuNames"]))
        if "default" in data:
            put(par, "default", data["default"])
            put(par, "val", data["default"])
    body: dict[str, Any] = {
        "success": True, "path": path, "page": page_name, "style": style,
        "params": [getattr(p, "name", name) for p in members],
    }
    if name != raw_name:
        body["renamed"] = f"{raw_name} -> {name} (TD requires Capitalized lowercase names)"
    if warnings:
        body["warnings"] = warnings
    return 200, body


# ---------------------------------------------------------------------------
# Endpoints — project & timeline
# ---------------------------------------------------------------------------

def _project_state() -> dict:
    td = _td()
    project, app = _tdg("project"), _tdg("app")
    out: dict[str, Any] = {}
    if project is not None:
        out["project"] = {a: v for a in ("name", "folder", "saveVersion", "cookRate", "realTime", "performMode")
                          if (v := _safe(project, a)) is not None}
    if app is not None:
        out["app"] = {a: v for a in ("product", "version", "build", "osName", "osVersion")
                      if (v := _safe(app, a)) is not None}
    root = td.op("/")
    t = getattr(root, "time", None) if root is not None else None
    if t is not None:
        out["timeline"] = {a: v for a in ("frame", "seconds", "play", "rate", "start", "end", "rangeStart", "rangeEnd")
                           if (v := _safe(t, a)) is not None}
    return out


@route("GET", "/project")
def _project_get(ctx: Ctx):
    """Project name/folder, TD version, cook rate, and timeline state."""
    return 200, _project_state()


@route("POST", "/project", timeout=30.0)
def _project_set(ctx: Ctx):
    """Timeline control: `play` (bool), `frame` (jump), `rate` (fps), and
    `save` (true = save in place, or a .toe path to save as)."""
    td = _td()
    data = ctx.json() or {}
    root = td.op("/")
    t = getattr(root, "time", None) if root is not None else None
    changed: dict[str, Any] = {}
    failed: dict[str, str] = {}
    for key in ("play", "frame", "rate"):
        if key not in data:
            continue
        try:
            if t is None:
                raise RuntimeError("timeline not available")
            val = bool(data[key]) if key == "play" else float(data[key])
            setattr(t, key, val)
            changed[key] = val
        except Exception as exc:  # noqa: BLE001
            failed[key] = str(exc)
    save = data.get("save")
    if save:
        try:
            project = _tdg("project")
            if project is None:
                raise RuntimeError("project not available")
            if isinstance(save, str):
                project.save(save)
            else:
                project.save()
            changed["save"] = save if isinstance(save, str) else True
        except Exception as exc:  # noqa: BLE001
            failed["save"] = str(exc)
    body: dict[str, Any] = {"changed": changed, **_project_state()}
    if failed:
        body["failed"] = failed
    return 200, body
