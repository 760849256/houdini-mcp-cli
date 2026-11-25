import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "python"))
from blib_hou_bridge import commands, protocol
from tests.test_blib_hou_bridge import FakeHou
from tests.test_viewport_framing import Viewport


class Vertex:
    def __init__(self, n): self.n = n
    def point(self): return self
    def number(self): return self.n


class Polygon:
    def __init__(self, points, closed=True): self.points, self.closed = points, closed
    def type(self): return "polygon"
    def vertices(self): return [Vertex(n) for n in self.points]
    def isClosed(self): return self.closed


class Geometry:
    def __init__(self, primitives): self.primitives = primitives
    def iterPrims(self): return iter(self.primitives)
    def pointAttribs(self): return []
    def vertexAttribs(self): return []
    def primAttribs(self): return []


class ExtendedViewport(Viewport):
    def type(self): return "old-type"
    def changeType(self, value): self.events.append(value)


class InspectionExtensionTests(unittest.TestCase):
    def test_boundary_nonmanifold_and_partial_scan(self):
        hou = SimpleNamespace(primType=SimpleNamespace(Polygon="polygon"))
        geo = Geometry([Polygon([0, 1, 2]), Polygon([1, 0, 3]), Polygon([0, 1, 4])])
        result = commands._geometry_health(hou, geo)
        self.assertTrue(result["complete"])
        self.assertEqual(result["non_manifold_edges"], 1)
        self.assertEqual(result["boundary_edges"], 6)
        result = commands._geometry_health(hou, geo, max_primitives=1)
        self.assertFalse(result["complete"])
        self.assertEqual(result["reason"], "primitive_limit")
        self.assertEqual(result["polygon_primitives_scanned"], 1)

    def test_open_polygon_does_not_add_closing_edge(self):
        hou = SimpleNamespace(primType=SimpleNamespace(Polygon="polygon"))
        result = commands._geometry_health(hou, Geometry([Polygon([0, 1, 2], False)]))
        self.assertEqual(result["boundary_edges"], 2)

    def test_view_and_time_restore_after_failure(self):
        hou = FakeHou()
        viewport = ExtendedViewport()
        viewport.fail = True
        hou.ui.scene_viewer.viewport = viewport
        hou.geometryViewportType = SimpleNamespace(Front="front-type", Right="right-type", Top="top-type")
        frames = []
        hou.frame = lambda: 17
        hou.setFrame = frames.append
        with self.assertRaises(RuntimeError):
            commands.execute("viewport_screenshot", {"view": "front", "timeline_frame": 25}, hou_module=hou)
        self.assertEqual(viewport.events, ["front-type", "draw", "old-type", "saved-view"])
        self.assertEqual(frames, [25, 17])

    def test_protocol_rejects_invalid_options(self):
        for payload in ({"view": "bad"}, {"timeline_frame": True}, {"timeline_frame": float("nan")}):
            with self.assertRaises(protocol.BridgeProtocolError):
                protocol.make_request("viewport_screenshot", payload)
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.make_request("geometry_summary", {"path": "/obj/a", "include_health": "yes"})


if __name__ == "__main__": unittest.main()
