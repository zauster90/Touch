"""Unit tests for td_layout — pure, no TouchDesigner required."""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "toe" / "src"))
import td_layout  # noqa: E402


def _chain(n):
    """A -> B -> C ... linear chain of n nodes."""
    nodes = [{"id": f"n{i}"} for i in range(n)]
    edges = [{"from": f"n{i}", "to": f"n{i+1}"} for i in range(n - 1)]
    return {"nodes": nodes, "edges": edges}


class LayoutTest(unittest.TestCase):
    def test_empty(self):
        r = td_layout.layout({"nodes": [], "edges": []})
        self.assertEqual(r["positions"], {})
        self.assertEqual(r["stats"]["nodes"], 0)

    def test_single_node(self):
        r = td_layout.layout({"nodes": [{"id": "a"}], "edges": []})
        self.assertEqual(r["positions"]["a"], (0, 0))
        self.assertEqual(r["stats"]["layers"], 1)

    def test_chain_ranks_increase_LR(self):
        r = td_layout.layout(_chain(4))
        xs = [r["positions"][f"n{i}"][0] for i in range(4)]
        # Left-to-right: each successive node strictly further right.
        self.assertEqual(xs, sorted(xs))
        self.assertEqual(len(set(xs)), 4)
        self.assertEqual(r["stats"]["layers"], 4)

    def test_chain_TB_uses_vertical_axis(self):
        r = td_layout.layout({**_chain(3), "direction": "TB"})
        ys = [r["positions"][f"n{i}"][1] for i in range(3)]
        # Top-to-bottom: y should be monotonic across ranks.
        self.assertTrue(ys == sorted(ys) or ys == sorted(ys, reverse=True))

    def test_diamond_no_overlap_positions(self):
        # a -> b, a -> c, b -> d, c -> d
        graph = {
            "nodes": [{"id": x} for x in "abcd"],
            "edges": [
                {"from": "a", "to": "b"}, {"from": "a", "to": "c"},
                {"from": "b", "to": "d"}, {"from": "c", "to": "d"},
            ],
        }
        r = td_layout.layout(graph)
        pos = r["positions"]
        # b and c share a rank (same x), different rows (different y).
        self.assertEqual(pos["b"][0], pos["c"][0])
        self.assertNotEqual(pos["b"][1], pos["c"][1])
        # a is left of b/c, d is right of b/c.
        self.assertLess(pos["a"][0], pos["b"][0])
        self.assertLess(pos["b"][0], pos["d"][0])

    def test_cycle_is_broken(self):
        # a -> b -> c -> a  (a 3-cycle); layout must remove one edge.
        graph = {
            "nodes": [{"id": x} for x in "abc"],
            "edges": [
                {"from": "a", "to": "b"}, {"from": "b", "to": "c"},
                {"from": "c", "to": "a"},
            ],
        }
        r = td_layout.layout(graph)
        self.assertEqual(len(r["broken_edges"]), 1)
        self.assertEqual(len(r["positions"]), 3)

    def test_feedback_top_edge_preferred_for_breaking(self):
        # Cycle a -> b -> a, with b a feedbackTOP. The break should touch b.
        graph = {
            "nodes": [{"id": "a"}, {"id": "b", "is_feedback_top": True}],
            "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}],
        }
        r = td_layout.layout(graph)
        self.assertEqual(len(r["broken_edges"]), 1)
        self.assertTrue(r["broken_edges"][0]["on_feedback_top"])

    def test_disconnected_components_dont_overlap_in_y(self):
        # Two separate chains -> stacked, not on top of each other.
        graph = {
            "nodes": [{"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": "d"}],
            "edges": [{"from": "a", "to": "b"}, {"from": "c", "to": "d"}],
        }
        r = td_layout.layout(graph)
        ys = {nid: p[1] for nid, p in r["positions"].items()}
        # The two components occupy disjoint y-bands.
        comp1 = {ys["a"], ys["b"]}
        comp2 = {ys["c"], ys["d"]}
        self.assertFalse(comp1 & comp2)

    def test_crossings_not_increased(self):
        # Bipartite tangle; reduction must not make crossings worse.
        nodes = [{"id": f"L{i}"} for i in range(3)] + [{"id": f"R{i}"} for i in range(3)]
        edges = [
            {"from": "L0", "to": "R2"}, {"from": "L1", "to": "R0"},
            {"from": "L2", "to": "R1"}, {"from": "L0", "to": "R0"},
        ]
        r = td_layout.layout({"nodes": nodes, "edges": edges})
        self.assertLessEqual(r["stats"]["crossings_after"], r["stats"]["crossings_before"])

    def test_size_aware_reports_no_overlaps(self):
        # Give real sizes; the final plan should be overlap-free.
        graph = {
            **_chain(5),
            "sizes": {f"n{i}": (200, 150) for i in range(5)},
        }
        r = td_layout.layout(graph)
        self.assertEqual(r["overlaps"], [])

    def test_too_large_rejected(self):
        graph = {"nodes": [{"id": f"n{i}"} for i in range(td_layout.MAX_NODES + 1)], "edges": []}
        r = td_layout.layout(graph)
        self.assertIn("error", r)
        self.assertEqual(r["error"]["type"], "TooLarge")

    def test_spacing_override_beats_size_aware(self):
        # When explicit spacing is given alongside sizes, fixed-step wins
        # (overlaps stay empty because size-aware detection is off).
        graph = {**_chain(3), "sizes": {f"n{i}": (50, 50) for i in range(3)},
                 "spacing": {"rank": 300, "node": 40}}
        r = td_layout.layout(graph)
        xs = sorted(r["positions"][f"n{i}"][0] for i in range(3))
        self.assertEqual(xs, [0, 300, 600])
        self.assertEqual(r["overlaps"], [])  # size-aware off -> no overlap scan


if __name__ == "__main__":
    unittest.main()
