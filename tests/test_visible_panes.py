import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts' / 'python'))
from blib_hou_bridge import commands
from blib_hou_mcp.server import BridgeMCPAdapter
from tests.test_blib_hou_bridge import FakeHou, FakeSceneViewer


class VisiblePaneTests(unittest.TestCase):
    def test_current_visible_viewer_wins_over_global_lookup(self):
        hou = FakeHou()
        hidden = FakeSceneViewer()
        hidden.isCurrentTab = lambda: False
        hou.ui.paneTabOfType = lambda _: hidden
        self.assertIs(commands._find_pane(hou, 'SceneViewer'), hou.ui.scene_viewer)

    def test_hidden_viewer_is_not_used_when_current_desktop_has_none(self):
        hou = FakeHou()
        hou.ui.desktop._panes = [hou.ui.network_editor]
        self.assertIsNone(commands._find_pane(hou, 'SceneViewer'))
        with self.assertRaises(commands.BridgeCommandError):
            commands.viewport_screenshot(hou, {})

    def test_inactive_tab_is_reported_uncapturable(self):
        hou = FakeHou()
        hou.ui.scene_viewer.isCurrentTab = lambda: False
        result = commands.list_panes(hou, {})
        viewer = next(p for p in result['panes'] if p['type'] == 'SceneViewer')
        self.assertFalse(viewer['capturable'])
        self.assertIsNone(commands._find_pane(hou, 'SceneViewer'))

    def test_pane_enumeration_does_not_depend_on_wrapper_identity(self):
        hou = FakeHou()
        with patch('builtins.id', return_value=1):
            result = commands.list_panes(hou, {})
        self.assertEqual(result['count'], 2)

    def test_black_viewport_is_not_reported_as_success(self):
        adapter = BridgeMCPAdapter()
        with patch.object(adapter, '_task_call', return_value={'ok': True, 'result': {'image_status': 'all_black', 'path': 'black.png'}}):
            result = adapter.call_tool('houdini_look', {})
        self.assertTrue(result['isError'])
        self.assertEqual(result['structuredContent']['error']['code'], 'unusable_preview')


if __name__ == '__main__':
    unittest.main()
