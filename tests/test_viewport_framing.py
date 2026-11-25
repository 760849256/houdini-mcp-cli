import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "python"))
from blib_hou_bridge import commands, protocol
from tests.test_blib_hou_bridge import FakeHou, FakeViewport


class Camera:
    def stash(self): return "saved-view"


class Viewport(FakeViewport):
    def __init__(self):
        self.events = []
        self.locked = False
        self.fail = False
    def camera(self): return None
    def isCameraLockedToView(self): return self.locked
    def defaultCamera(self): return Camera()
    def frameSelected(self): self.events.append("selected")
    def frameAll(self): self.events.append("all")
    def draw(self): self.events.append("draw")
    def setDefaultCamera(self, camera): self.events.append(camera)
    def saveViewToImage(self, path):
        if self.fail: raise RuntimeError("capture failed")
        super().saveViewToImage(path)


class ViewportFramingTests(unittest.TestCase):
    def test_frame_selected_and_restore_even_when_capture_fails(self):
        hou = FakeHou()
        viewport = Viewport()
        hou.ui.scene_viewer.viewport = viewport
        commands.execute("viewport_screenshot", {"frame": "selected"}, hou_module=hou)
        self.assertEqual(viewport.events, ["selected", "draw", "saved-view"])
        viewport.events = []
        viewport.fail = True
        with self.assertRaises(RuntimeError):
            commands.execute("viewport_screenshot", {"frame": "all"}, hou_module=hou)
        self.assertEqual(viewport.events, ["all", "draw", "saved-view"])

    def test_locked_camera_rejected_and_invalid_mode(self):
        hou = FakeHou()
        viewport = Viewport()
        viewport.locked = True
        hou.ui.scene_viewer.viewport = viewport
        with self.assertRaises(commands.BridgeCommandError):
            commands.execute("viewport_screenshot", {"frame": "selected"}, hou_module=hou)
        self.assertEqual(viewport.events, [])
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.make_request("viewport_screenshot", {"frame": "bad"})


if __name__ == "__main__": unittest.main()
