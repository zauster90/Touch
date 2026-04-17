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


class LayerAssignmentTest(unittest.TestCase):
    def test_two_node_chain(self):
        result = td_layout.layout({
            "nodes": [{"id": "a", "is_feedback_top": False},
                      {"id": "b", "is_feedback_top": False}],
            "edges": [{"from": "a", "to": "b"}],
        })
        # With direction LR and default rank spacing 150, a -> b means b is one rank right.
        self.assertEqual(result["stats"]["layers"], 2)
        self.assertLess(result["positions"]["a"][0], result["positions"]["b"][0])

    def test_diamond_has_b_and_c_on_same_rank(self):
        result = td_layout.layout({
            "nodes": [{"id": n, "is_feedback_top": False} for n in "abcd"],
            "edges": [{"from": "a", "to": "b"},
                      {"from": "a", "to": "c"},
                      {"from": "b", "to": "d"},
                      {"from": "c", "to": "d"}],
        })
        self.assertEqual(result["stats"]["layers"], 3)
        self.assertEqual(result["positions"]["b"][0], result["positions"]["c"][0])
        self.assertLess(result["positions"]["a"][0], result["positions"]["b"][0])
        self.assertLess(result["positions"]["b"][0], result["positions"]["d"][0])


class CycleBreakTest(unittest.TestCase):
    def test_pure_cycle_broken_by_edge_score(self):
        # a -> b -> c -> a. No feedback TOP. Engine picks an edge to break.
        result = td_layout.layout({
            "nodes": [{"id": i, "is_feedback_top": False} for i in "abc"],
            "edges": [{"from": "a", "to": "b"},
                      {"from": "b", "to": "c"},
                      {"from": "c", "to": "a"}],
        })
        self.assertEqual(len(result["broken_edges"]), 1)
        be = result["broken_edges"][0]
        self.assertFalse(be["on_feedback_top"])
        # All three still positioned:
        self.assertEqual(len(result["positions"]), 3)

    def test_cycle_with_feedback_top_breaks_at_feedback(self):
        # comp -> feedback -> comp. feedback is a feedbackTOP.
        result = td_layout.layout({
            "nodes": [
                {"id": "comp",     "is_feedback_top": False},
                {"id": "feedback", "is_feedback_top": True},
            ],
            "edges": [{"from": "comp",     "to": "feedback"},
                      {"from": "feedback", "to": "comp"}],
        })
        self.assertEqual(len(result["broken_edges"]), 1)
        be = result["broken_edges"][0]
        self.assertTrue(be["on_feedback_top"])
        # Engine preferred the edge touching feedback: feedback -> comp.
        self.assertEqual(be["from"], "feedback")
        self.assertEqual(be["to"], "comp")


class CrossingReductionTest(unittest.TestCase):
    def test_clean_diamond_has_zero_crossings(self):
        # a -> {b, c} -> d is crossing-free by construction.
        result = td_layout.layout({
            "nodes": [{"id": n, "is_feedback_top": False} for n in "abcd"],
            "edges": [{"from": "a", "to": "b"},
                      {"from": "a", "to": "c"},
                      {"from": "b", "to": "d"},
                      {"from": "c", "to": "d"}],
        })
        self.assertEqual(result["stats"]["crossings_after"], 0)

    def test_bipartite_crossing_is_reduced(self):
        # Two "left" nodes, two "right" nodes, wires x: a->d, b->c.
        # Natural ordering [a,b][c,d] has 1 crossing; barycenter should flip to [a,b][d,c] -> 0.
        result = td_layout.layout({
            "nodes": [{"id": n, "is_feedback_top": False} for n in "abcd"],
            "edges": [{"from": "a", "to": "d"},
                      {"from": "b", "to": "c"}],
        })
        self.assertEqual(result["stats"]["crossings_after"], 0)
        self.assertGreaterEqual(result["stats"]["crossings_before"], 1)


class BrandesKopfTest(unittest.TestCase):
    def test_parent_centered_over_children(self):
        # root -> {a, b, c}. root's y should be roughly the middle of a/b/c's y.
        nodes = [{"id": n, "is_feedback_top": False} for n in ("root", "a", "b", "c")]
        edges = [{"from": "root", "to": "a"},
                 {"from": "root", "to": "b"},
                 {"from": "root", "to": "c"}]
        result = td_layout.layout({"nodes": nodes, "edges": edges})
        p = result["positions"]
        ys = sorted([p["a"][1], p["b"][1], p["c"][1]])
        # Allow a small tolerance — Brandes-Kopf averages 4 alignments, so the root's y
        # should be within the span of the children.
        self.assertGreaterEqual(p["root"][1], ys[0])
        self.assertLessEqual(p["root"][1], ys[-1])

    def test_tb_direction_swaps_axes(self):
        result = td_layout.layout({
            "nodes": [{"id": "a", "is_feedback_top": False},
                      {"id": "b", "is_feedback_top": False}],
            "edges": [{"from": "a", "to": "b"}],
            "direction": "TB",
        })
        # TB: downstream node should have greater y (not x).
        self.assertEqual(result["positions"]["a"][0], result["positions"]["b"][0])
        self.assertLess(result["positions"]["a"][1], result["positions"]["b"][1])


if __name__ == "__main__":
    unittest.main()
