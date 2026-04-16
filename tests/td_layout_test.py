"""Unit tests for the pure-Python Sugiyama layout engine.
Runs with no TouchDesigner dependency."""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "toe" / "src"))
import td_layout  # noqa: E402


class TrivialTest(unittest.TestCase):
    def test_empty_graph(self):
        result = td_layout.layout({"nodes": [], "edges": []})
        self.assertEqual(result["positions"], {})
        self.assertEqual(result["broken_edges"], [])
        self.assertEqual(result["stats"]["nodes"], 0)
        self.assertEqual(result["stats"]["edges"], 0)
        self.assertEqual(result["stats"]["layers"], 0)

    def test_single_node_at_origin(self):
        result = td_layout.layout({"nodes": [{"id": "n", "is_feedback_top": False}], "edges": []})
        self.assertEqual(result["positions"], {"n": (0, 0)})
        self.assertEqual(result["stats"]["nodes"], 1)
        self.assertEqual(result["stats"]["layers"], 1)


if __name__ == "__main__":
    unittest.main()
