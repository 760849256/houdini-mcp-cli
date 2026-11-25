"""Run with hython in an isolated process; never edits a live UI session."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "python"))


def main():
    import hou
    from blib_hou_bridge import commands, state
    from blib_hou_mcp.server import BridgeMCPAdapter
    if hou.isUIAvailable():
        raise RuntimeError("Run this acceptance script in a separate hython process, not the user's live UI.")
    state.set_edit_enabled(True)
    requests = []
    def poster(session, request):
        requests.append(request["command"])
        try:
            return {"ok": True, "result": commands.execute(request["command"], request["payload"], hou_module=hou)}
        except Exception as exc:
            return {"ok": False, "result": {}, "error": {"message": str(exc)}}
    adapter = BridgeMCPAdapter(session_loader=lambda _: {"token": "isolated"}, poster=poster, tool_profile="daily")
    records = []
    def call(name, arguments):
        start = time.perf_counter()
        offset = len(requests)
        result = adapter.call_tool(name, arguments)
        records.append({"tool": name, "elapsed_ms": round((time.perf_counter() - start) * 1000, 2), "rpc_commands": requests[offset:], "ok": not result["isError"]})
        assert not result["isError"], result
        return result["structuredContent"]["result"]
    geo = hou.node('/obj').createNode('geo', 'daily_acceptance')
    try:
        source = geo.createNode('box', 'source')
        hou.clearAllSelected()
        source.setSelected(True)
        ctx = call('houdini_context', {})
        result = call('houdini_edit', {'context_token': ctx['context_token'], 'steps': [
            {'command': 'create_node', 'payload': {'type': 'normal', 'name': 'normal_preview'}},
            {'command': 'create_node', 'payload': {'type': 'null', 'name': 'OUT_PREVIEW'}},
            {'command': 'connect', 'payload': {'dst': '@selected_parent/normal_preview', 'input_index': 0}},
            {'command': 'connect', 'payload': {'src': '@selected_parent/normal_preview', 'dst': '@selected_parent/OUT_PREVIEW', 'input_index': 0}},
            {'command': 'set_flags', 'payload': {'node': '@selected_parent/OUT_PREVIEW', 'display': True, 'render': True}},
            {'command': 'layout', 'payload': {'path': '@selected_parent'}},
        ]})
        assert result['verified']
        output = geo.node('OUT_PREVIEW')
        geometry = output.geometry()
        assert len(geometry.points()) == 8
        assert geometry.findVertexAttrib('N') or geometry.findPointAttrib('N')
        # Explicit target framing bounds work for both SOP and object targets.
        assert commands._target_bounds(hou, output.path()).sizevec().length() > 0
        assert commands._target_bounds(hou, geo.path()).sizevec().length() > 0
        call('houdini_inspect', {'kind': 'geometry', 'targets': [output.path()], 'cook': True})
        call('houdini_edit', {'steps': [{'command': 'batch_set_parms', 'payload': {'values': {'sizex': 2, 'sizey': 3}}}]})
        assert source.parm('sizex').eval() == 2 and source.parm('sizey').eval() == 3
        call('houdini_inspect', {'kind': 'parms'})
        output.setInput(0, None)
        call('houdini_edit', {'steps': [{'command': 'connect', 'payload': {'src': geo.node('normal_preview').path(), 'dst': output.path(), 'input_index': 0}}]})
        assert output.input(0) == geo.node('normal_preview')
        call('houdini_inspect', {'kind': 'upstream', 'targets': [output.path()]})
        broken = geo.createNode('attribwrangle', 'intentional_error')
        broken.setInput(0, source)
        broken.parm('snippet').set('this is intentionally invalid VEX;')
        try:
            broken.cook(force=True)
        except hou.OperationFailed:
            pass
        assert broken.errors()
        diagnostics = call('houdini_inspect', {'kind': 'errors', 'targets': [geo.path()]})
        assert diagnostics['results'][0]['result']['error_count'] >= 1
        before = commands.context(hou)['context_token']
        hou.clearAllSelected()
        output.setSelected(True)
        rejected = adapter.call_tool('houdini_edit', {'context_token': before, 'steps': [{'command': 'set_comment', 'payload': {'comment': 'must not apply'}}]})
        assert rejected['isError'] and output.comment() != 'must not apply'
        print(json.dumps({'houdini': hou.applicationVersionString(), 'advertised_tools': len(adapter.list_tools()), 'tasks': ['node_chain', 'batch_parameters', 'repair_connection', 'find_errors'], 'stale_context_rejected': True, 'target_bounds_verified': True, 'visual_acceptance': 'pending_live_UI', 'timing_scope': 'in-process adapter/bridge, excludes network and model latency', 'calls': records}, indent=2))
    finally:
        geo.destroy()
        state.set_edit_enabled(False)
    # A same-name scene clear must invalidate previously observed context.
    token = commands.context(hou)['context_token']
    hou.hipFile.clear(suppress_save_prompt=True)
    assert commands.context(hou)['context_token'] != token
    print('DAILY_ACCEPTANCE_PASS')


if __name__ == '__main__':
    main()
