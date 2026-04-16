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


if __name__ == "__main__":
    unittest.main()
