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


class _FakePar:
    """Stand-in for a TD parameter. Has .val (mutable) and .eval (property)."""
    def __init__(self, val):
        self.val = val
    @property
    def eval(self):
        return self.val


class _FakeParGroup:
    """Stand-in for `op.par` — exposes parameters as attributes."""
    def __init__(self, initial: dict | None = None):
        self._pars: dict[str, _FakePar] = {}
        for k, v in (initial or {}).items():
            self._pars[k] = _FakePar(v)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if name not in self._pars:
            raise AttributeError(name)
        return self._pars[name]

    def __setattr__(self, name, value):
        if name == "_pars":
            object.__setattr__(self, name, value)
            return
        # Setting par creates or updates a _FakePar.
        self._pars[name] = _FakePar(value)

    def __dir__(self):
        return [n for n in self._pars.keys() if not n.startswith("_")]


class _FakeOp:
    def __init__(self, path="/", name="root", type_="root", family="COMP"):
        self.path = path
        self.name = name
        self.type = type_
        self.family = family
        self._children: list["_FakeOp"] = []
        self.par = _FakeParGroup({"tx": 0, "ty": 0})
        self.selected = False
        self.inputConnectors: list = []
        self.outputConnectors: list = []
        self.nodeX = 0
        self.nodeY = 0

    def findChildren(self, depth=1):
        out = list(self._children)
        if depth > 1:
            for c in self._children:
                out.extend(c.findChildren(depth=depth - 1))
        return out

    def errors(self): return ""
    def warnings(self): return ""

    def create(self, type_, name=None):
        child_name = name or type_
        child = _FakeOp(
            path=f"{self.path.rstrip('/')}/{child_name}",
            name=child_name, type_=type_, family="SOP",
        )
        self._children.append(child)
        _op_registry[child.path] = child
        return child

    def save(self, filepath):
        # Minimal PNG signature + padding so test_screenshot_png can detect it.
        Path(filepath).write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)


# Registry so repeated op(path) calls return the same instance.
_op_registry: dict[str, _FakeOp] = {}


def _stub_op(path=None):
    p = path or "/"
    if p not in _op_registry:
        _op_registry[p] = _FakeOp(path=p, name=p.rsplit("/", 1)[-1] or "root")
    return _op_registry[p]


_stub.op = _stub_op
_stub.ui = types.SimpleNamespace(panes=types.SimpleNamespace(current=types.SimpleNamespace(
    owner=_stub_op("/"), x=0, y=0, zoom=1.0)))
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
        # Register a dummy route so we get a 200 (a real success, not a deny path).
        @td_api.route("GET", "/_ping")
        def _ping(ctx):
            return 200, {"ok": True}
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        conn.request("GET", "/_ping", headers={
            "Host": "localhost",
            "Authorization": f"Bearer {self.token}",
        })
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertIsNone(resp.getheader("Access-Control-Allow-Origin"))
        conn.close()

    # --- read-only endpoints ---
    def test_pane_returns_state(self):
        status, body = self._req("/pane", token=self.token)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn("networkPath", data)
        self.assertIn("x", data)
        self.assertIn("y", data)
        self.assertIn("zoom", data)

    def test_selection_returns_list(self):
        status, body = self._req("/selection", token=self.token)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIsInstance(data["operators"], list)

    def test_operators_at_root(self):
        status, body = self._req("/operators?path=/", token=self.token)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["path"], "/")
        self.assertIsInstance(data["operators"], list)

    def test_operators_default_path_is_root(self):
        status, body = self._req("/operators", token=self.token)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["path"], "/")

    def test_errors_returns_structure(self):
        status, body = self._req("/errors", token=self.token)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn("errors", data)
        self.assertIn("warnings", data)
        self.assertIsInstance(data["errors"], list)
        self.assertIsInstance(data["warnings"], list)

    def test_execute_stdout(self):
        status, body = self._req(
            "/execute", token=self.token, method="POST",
            body="print('hi')")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["success"])
        self.assertIn("hi", data["stdout"])
        self.assertEqual(data["from_op"], "/")

    def test_execute_exception_returns_200_with_error(self):
        status, body = self._req(
            "/execute", token=self.token, method="POST",
            body="raise ValueError('boom')")
        self.assertEqual(status, 200)   # transport OK; code error surfaced in body
        data = json.loads(body)
        self.assertFalse(data["success"])
        self.assertEqual(data["error"]["type"], "ValueError")
        self.assertIn("boom", data["error"]["message"])

    def test_execute_with_from_op(self):
        status, body = self._req(
            "/execute?from_op=/foo", token=self.token, method="POST",
            body="print(me.path if me else 'no me')")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["success"])
        self.assertEqual(data["from_op"], "/foo")
        # stub op('/foo') returns a fresh _FakeOp with .path set to '/foo'
        self.assertIn("/foo", data["stdout"])

    def test_execute_captures_stderr(self):
        status, body = self._req(
            "/execute", token=self.token, method="POST",
            body="import sys; print('oops', file=sys.stderr)")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["success"])
        self.assertIn("oops", data["stderr"])

    def test_params_read(self):
        status, body = self._req("/params?path=/foo", token=self.token)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["path"], "/foo")
        self.assertIn("params", data)
        self.assertIn("tx", data["params"])

    def test_params_missing_path_returns_400(self):
        status, _ = self._req("/params", token=self.token)
        self.assertEqual(status, 400)

    def test_params_write(self):
        status, body = self._req(
            "/params?path=/bar", token=self.token, method="PATCH",
            body=json.dumps({"tx": 5}),
            headers={"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["applied"].get("tx"), 5)
        # Read it back
        status, body = self._req("/params?path=/bar", token=self.token)
        self.assertEqual(json.loads(body)["params"]["tx"], 5)

    def test_create_operator(self):
        payload = {"type": "geo", "parent": "/", "name": "geoA", "pos": [100, 200]}
        status, body = self._req(
            "/create", token=self.token, method="POST",
            body=json.dumps(payload),
            headers={"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["name"], "geoA")
        self.assertEqual(data["type"], "geo")

    def test_create_missing_type_returns_400(self):
        status, _ = self._req(
            "/create", token=self.token, method="POST",
            body=json.dumps({"parent": "/"}),
            headers={"Content-Type": "application/json"})
        self.assertEqual(status, 400)

    def test_graph(self):
        status, body = self._req("/graph?path=/&depth=2", token=self.token)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["path"], "/")
        self.assertEqual(data["depth"], 2)
        self.assertIsInstance(data["nodes"], list)

    def test_screenshot_png(self):
        # Create a TOP-like op that has .save(). Any op in our stub has .save.
        status, body = self._req("/screenshot?path=/topA", token=self.token)
        self.assertEqual(status, 200)
        self.assertEqual(body[:8], b"\x89PNG\r\n\x1a\n")

    def test_screenshot_missing_path_returns_400(self):
        status, _ = self._req("/screenshot", token=self.token)
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
