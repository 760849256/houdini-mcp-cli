import base64
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "python"))
from blib_hou_bridge import protocol
from blib_hou_mcp.server import BridgeMCPAdapter


class InlineViewportTests(unittest.TestCase):
    def test_multi_read_exposed_and_blocks_writes(self):
        adapter = BridgeMCPAdapter(session_loader=lambda path: None)
        names = {tool["name"] for tool in adapter.list_tools()}
        for name in ("houdini_multi_read", "houdini_diagnostic_bundle", "houdini_material_info", "houdini_render_info"):
            self.assertIn(name, names)
        protocol.validate_command("multi_read", {"steps": [{"command": "context"}, {"command": "selected"}]})
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.validate_command("multi_read", {"steps": [{"command": "create_node", "payload": {}}]})

    def test_viewport_result_attaches_only_screenshot_png(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            png = root / "view.png"
            data = b"\x89PNG\r\n\x1a\nimage-data"
            png.write_bytes(data)
            def poster(session, request):
                return {"ok": True, "result": {"path": str(png)}}
            adapter = BridgeMCPAdapter(session_loader=lambda path: {"host": "127.0.0.1", "port": 1, "token": "t"}, poster=poster)
            with patch("blib_hou_mcp.server._screenshot_root", return_value=root):
                response = adapter.call_tool("houdini_viewport_screenshot", {})
                self.assertEqual(response["content"][1]["type"], "image")
                self.assertEqual(base64.b64decode(response["content"][1]["data"]), data)
                with patch("blib_hou_mcp.server._screenshot_root", return_value=root / "elsewhere"):
                    denied = adapter.call_tool("houdini_viewport_screenshot", {})
                    self.assertFalse(any(item["type"] == "image" for item in denied["content"]))


if __name__ == "__main__":
    unittest.main()
