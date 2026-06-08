import json
import hashlib
import importlib.util
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path


ROOT = os.path.dirname(os.path.dirname(__file__))
PYTHON_DIR = os.path.join(ROOT, "scripts", "python")
CLI_PATH = os.path.join(ROOT, "scripts", "cli")


def _prepend_sys_path(path):
    while path in sys.path:
        sys.path.remove(path)
    sys.path.insert(0, path)


_prepend_sys_path(CLI_PATH)
_prepend_sys_path(PYTHON_DIR)

from blib_hou_bridge import auth, commands, dynamics_profiles, history, inspector, protocol, recipes, server, state, workflow_templates  # noqa: E402
from blib_hou_bridge import shelf  # noqa: E402
import blib_hou  # noqa: E402


def _load_tool_module(name):
    path = Path(ROOT) / "tools" / ("%s.py" % name)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeCategory:
    def name(self):
        return "Sop"


class FakeType:
    def __init__(self, name):
        self._name = name

    def name(self):
        return self._name

    def category(self):
        return FakeCategory()


class FakeParmType:
    def name(self):
        return "Float"


class FakeParmTemplate:
    def __init__(self, label):
        self._label = label

    def label(self):
        return self._label

    def type(self):
        return FakeParmType()


class FakeParm:
    def __init__(self, name, label):
        self._name = name
        self._label = label
        self.value = 1.25

    def name(self):
        return self._name

    def parmTemplate(self):
        return FakeParmTemplate(self._label)

    def isDisabled(self):
        return False

    def set(self, value):
        self.value = value

    def eval(self):
        return self.value

    def rawValue(self):
        return str(self.value)

    def expression(self):
        return ""

    def isLocked(self):
        return False

    def keyframes(self):
        return []


class FakeParmTemplateGroup:
    def __init__(self, node):
        self.node = node

    def append(self, template):
        self.node._parms.append(FakeParm(template.name, template.label))


class FakeNetworkBox:
    def __init__(self):
        self._name = "network_box"
        self._comment = ""
        self._nodes = []
        self._color = None
        self.fit_called = False

    def name(self):
        return self._name

    def setName(self, name):
        self._name = name

    def setComment(self, comment):
        self._comment = comment

    def setColor(self, color):
        self._color = color

    def addItem(self, item):
        self._nodes.append(item)

    def nodes(self):
        return self._nodes

    def fitAroundContents(self):
        self.fit_called = True


class FakeStickyNote:
    def __init__(self):
        self._name = "sticky_note"
        self._text = ""
        self._color = None
        self._position = [0.0, 0.0]

    def name(self):
        return self._name

    def setName(self, name):
        self._name = name

    def setText(self, text):
        self._text = text

    def setColor(self, color):
        self._color = color

    def setPosition(self, position):
        self._position = [float(position[0]), float(position[1])]


class FakeNode:
    def __init__(self, path, type_name="null", parent=None):
        self._path = path
        self._type = FakeType(type_name)
        self._parent = parent
        self._inputs = []
        self._outputs = []
        self._children = []
        self._parms = [FakeParm("tx", "Translate X")]
        self._add_type_parms(type_name)
        self._layout_called = False
        self._selected = False
        self._comment = ""
        self._display = True
        self._render = False
        self._bypass = False
        self._color = None
        self._position = [0.0, 0.0]
        self._user_data = {}
        self._network_boxes = []
        self._sticky_notes = []
        self._destroyed = False

    def path(self):
        return self._path

    def name(self):
        return self._path.rsplit("/", 1)[-1]

    def type(self):
        return self._type

    def parent(self):
        return self._parent

    def inputs(self):
        return self._inputs

    def outputs(self):
        return self._outputs

    def children(self):
        return [child for child in self._children if not child._destroyed]

    def parms(self):
        return self._parms

    def isDisplayFlagSet(self):
        return self._display

    def isRenderFlagSet(self):
        return self._render

    def isBypassed(self):
        return self._bypass

    def isSelected(self):
        return self._selected or self._path.endswith("OUT")

    def errors(self):
        return []

    def warnings(self):
        return []

    def networkBoxes(self):
        return self._network_boxes

    def createNode(self, node_type, node_name=None):
        name = node_name or ("%s1" % node_type)
        child = FakeNode("%s/%s" % (self._path, name), node_type, self)
        self._children.append(child)
        return child

    def parm(self, name):
        for parm in self._parms:
            if parm.name() == name:
                return parm
        return None

    def setInput(self, index, src):
        while len(self._inputs) <= index:
            self._inputs.append(None)
        old = self._inputs[index]
        if old is not None and self in old._outputs:
            old._outputs.remove(self)
        self._inputs[index] = src
        if src is not None and self not in src._outputs:
            src._outputs.append(self)

    def layoutChildren(self):
        self._layout_called = True

    def setSelected(self, selected):
        self._selected = bool(selected)

    def setComment(self, comment):
        self._comment = comment

    def comment(self):
        return self._comment

    def setDisplayFlag(self, enabled):
        self._display = bool(enabled)

    def setRenderFlag(self, enabled):
        self._render = bool(enabled)

    def setPosition(self, position):
        self._position = [float(position[0]), float(position[1])]

    def position(self):
        return self._position

    def setName(self, name, unique_name=True):
        parent_path = self._parent.path() if self._parent is not None else self._path.rsplit("/", 1)[0]
        self._set_path_recursive("%s/%s" % (parent_path.rstrip("/"), name))

    def moveToGoodPosition(self):
        return None

    def moveTo(self, parent):
        if self._parent is not None and self in self._parent._children:
            self._parent._children.remove(self)
        self._parent = parent
        if self not in parent._children:
            parent._children.append(self)
        self._set_path_recursive("%s/%s" % (parent.path().rstrip("/"), self.name()))

    def copyTo(self, parent):
        child = FakeNode("%s/%s_copy" % (parent.path(), self.name()), self._type.name(), parent)
        child._parms = [FakeParm(parm.name(), parm.parmTemplate().label()) for parm in self._parms]
        child._user_data = dict(self._user_data)
        parent._children.append(child)
        return child

    def destroy(self):
        for child in list(self.children()):
            child.destroy()
        for input_node in list(self._inputs):
            if input_node is not None and self in input_node._outputs:
                input_node._outputs.remove(self)
        for output_node in list(self._outputs):
            output_node._inputs = [None if item is self else item for item in output_node._inputs]
        if self._parent is not None and self in self._parent._children:
            self._parent._children.remove(self)
        self._inputs = []
        self._outputs = []
        self._destroyed = True

    def setUserData(self, key, value):
        self._user_data[key] = value

    def userData(self, key):
        return self._user_data.get(key)

    def setColor(self, color):
        self._color = color

    def color(self):
        return self._color

    def bypass(self, bypass):
        self._bypass = bool(bypass)

    def createNetworkBox(self):
        box = FakeNetworkBox()
        self._network_boxes.append(box)
        return box

    def createStickyNote(self):
        note = FakeStickyNote()
        self._sticky_notes.append(note)
        return note

    def parmTemplateGroup(self):
        return FakeParmTemplateGroup(self)

    def setParmTemplateGroup(self, group):
        return None

    def _add_type_parms(self, type_name):
        names = {
            "rbdmaterialfracture": ["fracturelevel", "materialtype"],
            "rbdconfigure": ["active", "density"],
            "connectadjacentpieces": ["searchradius"],
            "rbdconstraintproperties": ["strength", "constrainttype"],
            "rbdbulletsolver": ["startframe", "substeps"],
            "vellumconstraints_grain": ["particlesize", "friction"],
            "vellumconstraints": ["bendstiffness", "stretchstiffness", "friction"],
            "vellumsolver": ["startframe", "substeps", "collisionpasses"],
            "vellumpostprocess": ["thickness"],
            "volumerasterizeattributes": ["voxelsize", "densityscale"],
            "pyrosolver": ["startframe", "substeps", "buoyancy", "cooling", "dissipation", "disturbance", "turbulence"],
        }.get(type_name, [])
        for name in names:
            self._parms.append(FakeParm(name, name))

    def _set_path_recursive(self, path):
        old_path = self._path
        self._path = path
        for child in self._children:
            suffix = child.path()[len(old_path):].lstrip("/")
            child._set_path_recursive("%s/%s" % (path.rstrip("/"), suffix) if suffix else path)


class FakeNodeWithoutRenderFlag(FakeNode):
    def __getattribute__(self, name):
        if name == "isRenderFlagSet":
            raise AttributeError(name)
        return super().__getattribute__(name)


class FakeUndoGroup:
    def __init__(self, label):
        self.label = label

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeUndos:
    def group(self, label):
        return FakeUndoGroup(label)


class FakeHipFile:
    def path(self):
        return "D:/show/test.hip"

    def name(self):
        return "test.hip"


class FakePlaybar:
    def frameRange(self):
        return (1001.0, 1100.0)


class FakeViewport:
    def name(self):
        return "persp1"

    def saveViewToImage(self, path):
        with open(path, "wb") as handle:
            handle.write(b"fake-png")


class FakeSceneViewer:
    def __init__(self):
        self.viewport = FakeViewport()

    def curViewport(self):
        return self.viewport


class FakeUI:
    def __init__(self):
        self.scene_viewer = FakeSceneViewer()

    def paneTabOfType(self, pane_type):
        if pane_type == "SceneViewer":
            return self.scene_viewer
        return None


class FakeHou:
    class FloatParmTemplate:
        def __init__(self, name, label, size, default_value=(0.0,)):
            self.name = name
            self.label = label

    class IntParmTemplate:
        def __init__(self, name, label, size, default_value=(0,)):
            self.name = name
            self.label = label

    class Vector2(tuple):
        def __new__(cls, x, y):
            return tuple.__new__(cls, (x, y))

    class Color(tuple):
        def __new__(cls, values):
            return tuple.__new__(cls, values)

    class paneTabType:
        SceneViewer = "SceneViewer"
        NetworkEditor = "NetworkEditor"

    def __init__(self):
        self.hipFile = FakeHipFile()
        self.playbar = FakePlaybar()
        self.undos = FakeUndos()
        self.ui = FakeUI()
        self.root = FakeNode("/obj", "obj")
        self.geo = FakeNode("/obj/geo1", "geo", self.root)
        self.box = FakeNode("/obj/geo1/box1", "box", self.geo)
        self.out = FakeNode("/obj/geo1/OUT", "null", self.geo)
        self.wrangle = FakeNode("/obj/geo1/set_pscale1", "attribwrangle", self.geo)
        self.root._children = [self.geo]
        self.out._inputs = [self.box]
        self.box._outputs = [self.out]
        self.wrangle._inputs = [self.out]
        self.out._outputs = [self.wrangle]
        self.geo._children = [self.box, self.out, self.wrangle]
        self.nodes = {
            "/obj": self.root,
            "/obj/geo1": self.geo,
            "/obj/geo1/box1": self.box,
            "/obj/geo1/OUT": self.out,
            "/obj/geo1/set_pscale1": self.wrangle,
        }

    def selectedNodes(self):
        return [self.out]

    def node(self, path):
        self._refresh_nodes()
        node = self.nodes.get(path)
        if node is not None and not node._destroyed and node.path() == path:
            return node
        return None

    def _refresh_nodes(self):
        nodes = {}
        stack = [self.root]
        while stack:
            node = stack.pop(0)
            if node._destroyed:
                continue
            nodes[node.path()] = node
            stack.extend(node.children())
        self.nodes = nodes

    def frame(self):
        return 1001.0

    def applicationVersionString(self):
        return "21.0.440"

    def pwd(self):
        return self.geo


class FakeShelfUI:
    def __init__(self, choice=0):
        self.choice = choice
        self.messages = []

    def displayMessage(self, message, **kwargs):
        self.messages.append({"message": message, "kwargs": kwargs})
        return self.choice


class FakeShelfHou:
    def __init__(self, choice=0):
        self.ui = FakeShelfUI(choice)


class BlibHouBridgeTests(unittest.TestCase):
    def tearDown(self):
        state.set_edit_enabled(False)
        history.clear()
        server.stop_server()

    def test_protocol_is_readonly_and_blocks_danger_commands(self):
        self.assertIn("context", protocol.READ_COMMANDS)
        self.assertIn("manifest", protocol.READ_COMMANDS)
        self.assertIn("profile_manifest", protocol.READ_COMMANDS)
        self.assertIn("probe_parm_profile", protocol.READ_COMMANDS)
        self.assertIn("recipe_manifest", protocol.READ_COMMANDS)
        self.assertIn("review_plan", protocol.READ_COMMANDS)
        self.assertIn("verify_plan", protocol.READ_COMMANDS)
        self.assertIn("create_node", protocol.EDIT_COMMANDS)
        self.assertIn("run_plan", protocol.EDIT_COMMANDS)
        self.assertIn("apply_parm_profile", protocol.EDIT_COMMANDS)
        self.assertIn("delete_node", protocol.EDIT_COMMANDS)
        self.assertNotIn("delete_node", protocol.DANGER_COMMANDS)
        for danger in protocol.DANGER_COMMANDS:
            with self.subTest(danger=danger):
                with self.assertRaises(protocol.BridgeProtocolError):
                    protocol.validate_command(danger, {})

    def test_manifest_is_readonly_and_self_describing(self):
        request = protocol.make_request("manifest")
        self.assertEqual(request["command"], "manifest")

        result = commands.execute("manifest")
        self.assertEqual(result["version"], protocol.BRIDGE_VERSION)
        self.assertEqual(set(result["commands"]), protocol.READ_COMMANDS | protocol.EDIT_COMMANDS)
        for command in result["commands"].values():
            self.assertIn("payload_schema", command)
            self.assertIn("result_schema", command)
            self.assertIn("exposure", command)
            self.assertIn("mcp_tool_name", command)
        self.assertEqual(result["commands"]["manifest"]["permission"], "read")
        self.assertEqual(result["commands"]["manifest"]["exposure"], "read")
        self.assertEqual(result["commands"]["scene_snapshot"]["permission"], "read")
        scene_result_schema = result["commands"]["scene_snapshot"]["result_schema"]
        self.assertIn("focus_candidates", scene_result_schema["properties"]["semantics"]["properties"])
        self.assertIn("inspection_hints", scene_result_schema["properties"]["semantics"]["properties"])
        self.assertIn("scene_understanding", scene_result_schema["properties"]["semantics"]["properties"])
        self.assertIn("volume_nodes", scene_result_schema["properties"]["semantics"]["properties"])
        self.assertIn("risk_domains", scene_result_schema["properties"]["semantics"]["properties"])
        self.assertIn("workflow_suggestions", scene_result_schema["properties"]["semantics"]["properties"])
        self.assertIn("focus_candidate_count", scene_result_schema["properties"]["summary"]["properties"])
        for key in ("cache_node_count", "simulation_node_count", "volume_node_count", "render_node_count"):
            self.assertIn(key, scene_result_schema["properties"]["summary"]["properties"])
        self.assertIn("risk_domain_count", scene_result_schema["properties"]["summary"]["properties"])
        self.assertIn("workflow_suggestion_count", scene_result_schema["properties"]["summary"]["properties"])
        node_info_result_schema = result["commands"]["node_info"]["result_schema"]
        for key in ("path", "name", "type", "category", "parent", "inputs", "outputs", "children", "parms", "flags", "comment", "position", "color", "messages"):
            self.assertIn(key, node_info_result_schema["properties"])
        for key in ("display", "render", "bypass", "selected"):
            self.assertIn(key, node_info_result_schema["properties"]["flags"]["properties"])
        validate_result_schema = result["commands"]["validate_plan"]["result_schema"]
        for key in ("valid", "ready_to_run", "would_require_edit", "blocked_by_edit_mode", "step_count", "steps_sha256", "steps"):
            self.assertIn(key, validate_result_schema["properties"])
        review_result_schema = result["commands"]["review_plan"]["result_schema"]
        for key in ("level", "confidence", "blockers", "warnings", "suggestions", "impact", "risk_notes", "required_confirmations"):
            self.assertIn(key, review_result_schema["properties"])
        run_result_schema = result["commands"]["run_plan"]["result_schema"]
        for key in ("ok", "count", "ran", "stopped", "failed_step", "results"):
            self.assertIn(key, run_result_schema["properties"])
        verify_result_schema = result["commands"]["verify_plan"]["result_schema"]
        for key in ("ok", "verified", "status", "summary", "checks", "validation", "run"):
            self.assertIn(key, verify_result_schema["properties"])
        self.assertEqual(result["commands"]["validate_plan"]["permission"], "read")
        self.assertEqual(result["commands"]["recipe_manifest"]["permission"], "read")
        self.assertEqual(result["commands"]["profile_manifest"]["permission"], "read")
        self.assertEqual(result["commands"]["probe_parm_profile"]["permission"], "read")
        self.assertEqual(result["commands"]["review_plan"]["permission"], "read")
        self.assertEqual(result["commands"]["verify_plan"]["permission"], "read")
        self.assertEqual(result["commands"]["create_node"]["permission"], "edit")
        self.assertEqual(result["commands"]["create_node"]["exposure"], "direct_edit")
        self.assertEqual(result["commands"]["rename_node"]["permission"], "edit")
        self.assertEqual(result["commands"]["rename_node"]["exposure"], "plan_required")
        self.assertEqual(result["commands"]["create_network_box"]["permission"], "edit")
        self.assertEqual(result["commands"]["create_network_box"]["exposure"], "plan_required")
        self.assertEqual(result["commands"]["run_plan"]["permission"], "edit")
        self.assertEqual(result["commands"]["run_plan"]["exposure"], "plan_required")
        self.assertEqual(result["commands"]["apply_parm_profile"]["permission"], "edit")
        self.assertEqual(result["commands"]["batch_set_parms"]["permission"], "edit")
        self.assertEqual(result["commands"]["batch_set_parms"]["exposure"], "direct_edit")
        self.assertEqual(result["commands"]["delete_node"]["permission"], "edit")
        self.assertEqual(result["commands"]["delete_node"]["exposure"], "plan_required")
        self.assertEqual(result["commands"]["replace_node"]["permission"], "edit")
        self.assertIn("create_node", result["direct_edit_commands"])
        self.assertIn("set_comment", result["direct_edit_commands"])
        self.assertEqual(
            set(result["safety_policy"]["edit_gate"]["direct_edit_verification"]),
            set(result["direct_edit_commands"]),
        )
        self.assertEqual(set(protocol.DIRECT_EDIT_VERIFICATION_CONTRACTS), protocol.DIRECT_EDIT_COMMANDS)
        for command_name in result["direct_edit_commands"]:
            verification = result["commands"][command_name]["verification"]
            self.assertTrue(verification["requires_readback"])
            self.assertFalse(verification["may_report_success_from_rpc_ok"])
            self.assertTrue(verification["read_tools"])
            self.assertTrue(verification["success_criteria"])
            for read_tool in verification["read_tools"]:
                self.assertIn(read_tool, protocol.READ_COMMANDS)
            self.assertEqual(verification["mcp_read_tools"], ["houdini_%s" % tool for tool in verification["read_tools"]])
        set_comment_verification = result["commands"]["set_comment"]["verification"]
        self.assertTrue(set_comment_verification["requires_readback"])
        self.assertFalse(set_comment_verification["may_report_success_from_rpc_ok"])
        self.assertEqual(set_comment_verification["read_tools"], ["node_info"])
        self.assertEqual(set_comment_verification["mcp_read_tools"], ["houdini_node_info"])
        self.assertIn("node_info.comment", set_comment_verification["success_criteria"][0])
        self.assertIn("delete_node", result["plan_required_edit_commands"])
        self.assertIn("replace_node", result["plan_required_edit_commands"])
        self.assertEqual(result["safety_policy"]["transport"]["host"], "127.0.0.1")
        self.assertTrue(result["safety_policy"]["transport"]["token_required"])
        self.assertIn("create_node", result["safety_policy"]["edit_gate"]["direct_edit_commands"])
        self.assertEqual(
            result["safety_policy"]["edit_gate"]["direct_edit_verification"]["set_comment"],
            set_comment_verification,
        )
        self.assertIn("delete_node", result["safety_policy"]["edit_gate"]["plan_required_edit_commands"])
        self.assertIn("run_python", result["safety_policy"]["blocked"]["danger_commands"])
        self.assertEqual(
            result["safety_policy"]["edit_gate"]["required_plan_flow"],
            ["review_plan", "validate_plan", "run_plan", "verify_plan"],
        )
        self.assertEqual(result["commands"]["network"]["payload_schema"]["required"], ["path"])
        self.assertEqual(result["commands"]["validate_plan"]["payload_schema"]["required"], ["steps"])
        self.assertEqual(result["commands"]["review_plan"]["payload_schema"]["required"], ["steps"])
        self.assertEqual(result["commands"]["verify_plan"]["payload_schema"]["required"], ["steps"])
        self.assertNotIn("maxItems", result["commands"]["validate_plan"]["payload_schema"]["properties"]["steps"])
        self.assertNotIn("maxItems", result["commands"]["review_plan"]["payload_schema"]["properties"]["steps"])
        self.assertEqual(result["commands"]["find_nodes"]["payload_schema"]["required"], ["root"])
        self.assertEqual(result["commands"]["set_flags"]["payload_schema"]["required"], ["node"])
        self.assertEqual(result["commands"]["node_parms"]["payload_schema"]["required"], ["path"])
        self.assertEqual(result["commands"]["run_plan"]["payload_schema"]["required"], ["steps"])
        self.assertEqual(result["commands"]["apply_parm_profile"]["payload_schema"]["required"], ["node", "profile"])
        self.assertEqual(result["commands"]["probe_parm_profile"]["payload_schema"]["required"], ["node", "profile"])
        self.assertEqual(result["commands"]["batch_set_parms"]["payload_schema"]["required"], ["node", "values"])
        self.assertEqual(result["commands"]["delete_node"]["payload_schema"]["required"], ["node", "confirm"])
        self.assertEqual(result["commands"]["replace_node"]["payload_schema"]["required"], ["node", "type"])
        self.assertIn("rpc_log", result["commands"])
        self.assertIn("run_python", result["danger_commands"])

    def test_recipe_manifest_is_bridge_native_and_readonly(self):
        result = commands.execute("recipe-manifest")
        self.assertEqual(result["version"], 1)
        self.assertIn("build_rbd_fracture_setup", result["contracts"])
        self.assertIn("preview", result["presets"])
        self.assertIn("production", result["presets"])
        self.assertIn("prod", result["preset_aliases"])
        self.assertEqual(result["contracts"], recipes.RECIPE_CONTRACTS)
        self.assertIn("do not execute Agent actions", result["note"])

    def test_profile_manifest_is_readonly_and_self_describing(self):
        result = commands.execute("profile-manifest")
        self.assertIn("rbd-fracture-preview", result["profiles"])
        self.assertIn("pyro-solver-preview", result["profile_names"])
        fracture = result["profiles"]["rbd-fracture-preview"]["parameters"]
        self.assertIn("detail_size", fracture)
        self.assertIn("fracturelevel", fracture["detail_size"]["candidates"])

    def test_workflow_templates_generate_protocol_valid_bridge_steps(self):
        catalog = workflow_templates.template_catalog()
        self.assertEqual(catalog["version"], 1)
        self.assertIn("sop-cleanup", catalog["template_names"])
        self.assertIn("karma-solaris-preview", catalog["template_names"])
        self.assertEqual(catalog["templates"]["sop-cleanup"]["category"], "cleanup")
        self.assertEqual(catalog["templates"]["karma-solaris-preview"]["category"], "render")
        self.assertEqual(catalog["workflow_policy"]["required_flow"], workflow_templates.TEMPLATE_REQUIRED_FLOW)
        self.assertTrue(catalog["workflow_policy"]["local_generation_only"])
        self.assertTrue(catalog["workflow_policy"]["does_not_execute"])
        self.assertEqual(catalog["templates"]["rbd-preview"]["execution"]["required_flow"], workflow_templates.TEMPLATE_REQUIRED_FLOW)
        self.assertIn("simulation_settings", catalog["templates"]["rbd-preview"]["risk_domains"])
        self.assertIn("verification", catalog["templates"]["rbd-preview"]["evidence_expectations"])
        self.assertTrue(catalog["workflow_policy"]["verification_focus_required"])
        self.assertIn("verification_focus", catalog["templates"]["rbd-preview"])
        self.assertIn("houdini_node_parms", catalog["templates"]["rbd-preview"]["verification_focus"]["read_tools"])
        self.assertIn("simulation_nodes_exist", catalog["templates"]["rbd-preview"]["verification_focus"]["success_criteria"])
        self.assertIn("no_render_execution_claimed", catalog["templates"]["karma-solaris-preview"]["verification_focus"]["success_criteria"])
        self.assertIn("default_karma_render_path", catalog)
        self.assertIn("preview", catalog["presets"])
        catalog["presets"]["preview"]["cleanup"]["fuse_distance"] = 99
        self.assertNotEqual(workflow_templates.template_catalog()["presets"]["preview"]["cleanup"]["fuse_distance"], 99)

        allowed = {
            "create_node",
            "connect",
            "set_parm_any",
            "apply_parm_profile",
            "set_node_color",
            "set_comment",
            "set_flags",
            "create_sticky_note",
            "layout",
            "select",
        }
        for template in workflow_templates.TEMPLATE_NAMES:
            with self.subTest(template=template):
                plan = workflow_templates.build_plan(template, "/obj/geo1/OUT", {"preset": "preview"})
                self.assertTrue(plan)
                normalized = [protocol.normalize_command(step["command"]) for step in plan]
                self.assertTrue(set(normalized) <= allowed)
                self.assertIn("layout", normalized)
                self.assertEqual(normalized[-1], "select")
                self.assertFalse(any(command == "create_network_box" for command in normalized))
                for step in plan:
                    protocol.validate_command(step["command"], step.get("payload", {}))

    def test_workflow_template_options_set_names_and_outputs(self):
        plan = workflow_templates.build_plan(
            "sop-cleanup",
            "/obj/geo1/OUT",
            {"name": "quick", "output_name": "OUT_QUICK", "fuse_distance": 0.05},
        )
        names = [step.get("payload", {}).get("name") for step in plan]
        self.assertIn("QUICK_CLEAN", names)
        self.assertIn("OUT_QUICK", names)
        fuse_step = next(step for step in plan if step["command"] == "set-parm-any" and "dist" in step["payload"]["parms"])
        self.assertEqual(fuse_step["payload"]["value"], 0.05)

    def test_workflow_template_builds_karma_solaris_preview_without_render_execution(self):
        plan = workflow_templates.build_plan(
            "karma-solaris-preview",
            "/obj/geo1/OUT",
            {
                "preset": "production",
                "name": "shot",
                "render_path": "$HIP/render/shot/beauty.$F4.exr",
                "camera_path": "/cameras/render_cam",
                "resolution": "2048x1152",
                "samples": 64,
                "lopnet_name": "SHOT_STAGE",
            },
        )

        normalized = [protocol.normalize_command(step["command"]) for step in plan]
        self.assertIn("create_node", normalized)
        self.assertIn("connect", normalized)
        self.assertIn("set_parm_any", normalized)
        self.assertIn("layout", normalized)
        self.assertEqual(normalized[-1], "select")
        self.assertNotIn("run_python", normalized)
        self.assertNotIn("save_hip", normalized)
        self.assertFalse(any("render" == step.get("payload", {}).get("parm") for step in plan))
        payload_text = json.dumps(plan)
        self.assertIn("/obj/SHOT_STAGE/SHOT_USD_RENDER_ROP", payload_text)
        self.assertIn("$HIP/render/shot/beauty.$F4.exr", payload_text)
        self.assertIn("/cameras/render_cam", payload_text)
        self.assertIn('"value": 2048', payload_text)
        self.assertIn('"value": 1152', payload_text)
        self.assertIn('"value": 64', payload_text)
        self.assertIn("not executed", payload_text)
        for step in plan:
            protocol.validate_command(step["command"], step.get("payload", {}))
        validation = commands.execute("validate-plan", {"steps": plan}, hou_module=FakeHou())
        self.assertTrue(validation["valid"])
        self.assertTrue(validation["would_require_edit"])
        self.assertEqual(validation["step_count"], len(plan))

    def test_workflow_templates_use_dynamics_profiles_for_rbd_vellum_pyro(self):
        for template in ("rbd-preview", "vellum-grains-preview", "vellum-cloth-preview", "pyro-source-preview"):
            with self.subTest(template=template):
                plan = workflow_templates.build_plan(
                    template,
                    "/obj/geo1/OUT",
                    {"preset": "preview", "substeps": 5, "start_frame": 1001, "constraint_strength": 42, "dissipation": 0.3},
                )
                profile_steps = [step for step in plan if protocol.normalize_command(step["command"]) == "apply_parm_profile"]
                self.assertTrue(profile_steps)
                for step in profile_steps:
                    self.assertIn(step["payload"]["profile"], dynamics_profiles.PROFILE_NAMES)
                    protocol.validate_command(step["command"], step["payload"])

    def test_inspector_snapshot_reads_bridge_state_without_scene_edits(self):
        fake_hou = FakeHou()
        history.record({"command": "context", "ok": True, "status": 200})
        snapshot = inspector.build_snapshot(fake_hou)
        self.assertEqual(snapshot["context"]["current_network"], "/obj/geo1")
        self.assertEqual(snapshot["selected"]["count"], 1)
        self.assertEqual(snapshot["network"]["node_count"], 3)
        self.assertIn("manifest", snapshot["manifest"]["commands"])
        self.assertEqual(snapshot["rpc_log"]["count"], 1)
        overview = inspector.format_overview(snapshot)
        self.assertIn("Bridge", overview)
        self.assertIn("Current network: /obj/geo1", overview)
        self.assertIn("Recent events: 1", overview)

        details = {
            "node_info": commands.execute("node-info", {"path": "/obj/geo1/OUT"}, hou_module=fake_hou),
            "node_parms": commands.execute("node-parms", {"path": "/obj/geo1/OUT"}, hou_module=fake_hou),
        }
        text = inspector.format_node_details(details)
        self.assertIn("Path: /obj/geo1/OUT", text)
        self.assertIn("Parameters (1)", text)

    def test_node_info_requires_absolute_path(self):
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.make_request("node-info", {"path": "obj/geo1"})
        request = protocol.make_request("node-info", {"path": "/obj/geo1"})
        self.assertEqual(request["command"], "node_info")

    def test_commands_read_fake_houdini_context_without_writes(self):
        fake_hou = FakeHou()
        context = commands.execute("context", hou_module=fake_hou)
        self.assertEqual(context["hip_name"], "test.hip")
        self.assertEqual(context["current_network"], "/obj/geo1")
        self.assertEqual(context["selection"][0]["path"], "/obj/geo1/OUT")

        selected = commands.execute("selected", hou_module=fake_hou)
        self.assertEqual(selected["count"], 1)

        found = commands.execute("find-nodes", {"root": "/obj", "type": "null"}, hou_module=fake_hou)
        self.assertEqual(found["count"], 1)
        self.assertEqual(found["nodes"][0]["path"], "/obj/geo1/OUT")

        found = commands.execute("find-nodes", {"root": "/obj", "name": "geo", "limit": 2}, hou_module=fake_hou)
        self.assertGreaterEqual(found["count"], 1)
        self.assertEqual(found["nodes"][0]["path"], "/obj/geo1")

        info = commands.execute("node-info", {"path": "/obj/geo1"}, hou_module=fake_hou)
        self.assertEqual(info["path"], "/obj/geo1")
        self.assertEqual(info["children"][1]["path"], "/obj/geo1/OUT")
        self.assertEqual(info["parms"][0]["name"], "tx")

        parms = commands.execute("node-parms", {"path": "/obj/geo1"}, hou_module=fake_hou)
        self.assertEqual(parms["node"]["path"], "/obj/geo1")
        self.assertEqual(parms["parm_count"], 1)
        self.assertEqual(parms["parms"][0]["name"], "tx")
        self.assertEqual(parms["parms"][0]["value"], 1.25)
        self.assertFalse(parms["parms"][0]["locked"])

    def test_scene_snapshot_bundles_current_network_selection_and_traces(self):
        fake_hou = FakeHou()
        snapshot = commands.execute(
            "scene-snapshot",
            {"path": "/obj/geo1", "trace_depth": 1, "max_selected": 2},
            hou_module=fake_hou,
        )
        self.assertEqual(snapshot["context"]["current_network"], "/obj/geo1")
        self.assertEqual(snapshot["network"]["node_count"], 3)
        self.assertEqual(snapshot["selected"]["nodes"][0]["path"], "/obj/geo1/OUT")
        self.assertEqual(snapshot["selected_details"][0]["path"], "/obj/geo1/OUT")
        self.assertEqual(snapshot["traces"][0]["path"], "/obj/geo1/OUT")
        self.assertFalse(snapshot["viewport"]["included"])
        self.assertEqual(snapshot["summary"]["network_path"], "/obj/geo1")
        self.assertEqual(snapshot["summary"]["trace_count"], 1)
        self.assertEqual(snapshot["summary"]["inferred_purpose"], "sop_or_general_node_network")
        self.assertEqual(snapshot["summary"]["key_output_count"], len(snapshot["semantics"]["key_outputs"]))
        self.assertEqual(snapshot["summary"]["cache_node_count"], len(snapshot["semantics"]["cache_nodes"]))
        self.assertEqual(snapshot["summary"]["simulation_node_count"], len(snapshot["semantics"]["simulation_nodes"]))
        self.assertEqual(snapshot["summary"]["volume_node_count"], len(snapshot["semantics"]["volume_nodes"]))
        self.assertEqual(snapshot["summary"]["render_node_count"], len(snapshot["semantics"]["render_nodes"]))
        self.assertEqual(snapshot["summary"]["focus_candidate_count"], len(snapshot["semantics"]["focus_candidates"]))
        self.assertEqual(snapshot["summary"]["risk_domain_count"], len(snapshot["semantics"]["risk_domains"]))
        self.assertEqual(snapshot["summary"]["workflow_suggestion_count"], len(snapshot["semantics"]["workflow_suggestions"]))
        self.assertIn("/obj/geo1/OUT", [item["path"] for item in snapshot["semantics"]["key_outputs"]])
        focus_by_path = {item["path"]: item for item in snapshot["semantics"]["focus_candidates"]}
        self.assertIn("/obj/geo1/OUT", focus_by_path)
        self.assertEqual(focus_by_path["/obj/geo1/OUT"]["priority"], "high")
        self.assertIn("selected", focus_by_path["/obj/geo1/OUT"]["kinds"])
        self.assertIn("output", focus_by_path["/obj/geo1/OUT"]["kinds"])
        self.assertIn("houdini_node_parms", focus_by_path["/obj/geo1/OUT"]["mcp_tools"])
        self.assertEqual(snapshot["semantics"]["selected_focus"][0]["path"], "/obj/geo1/OUT")
        self.assertGreaterEqual(snapshot["semantics"]["selected_focus"][0]["upstream_node_count"], 2)
        self.assertGreaterEqual(snapshot["semantics"]["selected_focus"][0]["downstream_node_count"], 2)
        self.assertEqual(snapshot["semantics"]["network_shape"]["wire_count"], 2)
        self.assertEqual(snapshot["semantics"]["network_shape"]["type_counts"]["null"], 1)
        self.assertEqual(snapshot["summary"]["inspection_hint_count"], len(snapshot["semantics"]["inspection_hints"]))
        hint_pairs = {(item["command"], item["payload"].get("path")) for item in snapshot["semantics"]["inspection_hints"]}
        self.assertIn(("node_info", "/obj/geo1/OUT"), hint_pairs)
        self.assertIn(("node_parms", "/obj/geo1/OUT"), hint_pairs)
        self.assertIn(("upstream", "/obj/geo1/OUT"), hint_pairs)
        self.assertTrue(all(item["mcp_tool"].startswith("houdini_") for item in snapshot["semantics"]["inspection_hints"]))
        understanding = snapshot["semantics"]["scene_understanding"]
        self.assertEqual(understanding["state"], "risk_domain_detected")
        self.assertEqual(understanding["network_path"], "/obj/geo1")
        self.assertEqual(understanding["inferred_purpose"], "sop_or_general_node_network")
        self.assertEqual(understanding["primary_focus_path"], "/obj/geo1/OUT")
        self.assertIn("houdini_node_parms", understanding["first_read_tools"])
        self.assertIn("houdini_review_plan", understanding["required_write_flow"])
        self.assertFalse(understanding["may_execute"])
        self.assertFalse(understanding["safe_to_run_direct_edits"])
        self.assertTrue(understanding["requires_user_approval_for_writes"])
        self.assertEqual(understanding["read_targets"][0]["path"], "/obj/geo1/OUT")
        suggestions = snapshot["semantics"]["workflow_suggestions"]
        self.assertEqual(suggestions[0]["template"], "sop-cleanup")
        self.assertEqual(suggestions[0]["mcp_tool"], "houdini_template_plan")
        self.assertEqual(suggestions[0]["template_arguments"]["input"], "/obj/geo1/OUT")
        self.assertEqual(
            suggestions[0]["required_flow"],
            ["houdini_review_plan", "houdini_validate_plan", "houdini_run_plan", "houdini_verify_plan"],
        )
        self.assertTrue(suggestions[0]["local_generation_only"])

    def test_scene_snapshot_tolerates_nodes_without_render_flag_api(self):
        fake_hou = FakeHou()
        dop_like = FakeNodeWithoutRenderFlag("/obj/geo1/DOP_LIKE", "dopnet", fake_hou.geo)
        fake_hou.geo._children.append(dop_like)
        fake_hou._refresh_nodes()

        snapshot = commands.execute("scene-snapshot", {"path": "/obj/geo1"}, hou_module=fake_hou)

        self.assertEqual(snapshot["network"]["node_count"], 4)
        self.assertIn("/obj/geo1/DOP_LIKE", [item["path"] for item in snapshot["network"]["nodes"]])
        self.assertNotIn("/obj/geo1/DOP_LIKE", snapshot["network"]["render_nodes"])
        self.assertEqual(snapshot["summary"]["network_path"], "/obj/geo1")
        self.assertIn("scene_understanding", snapshot["semantics"])

    def test_scene_snapshot_semantics_identify_cache_and_simulation_nodes(self):
        fake_hou = FakeHou()
        cache = FakeNode("/obj/geo1/CACHE_OUT", "filecache", fake_hou.geo)
        solver = FakeNode("/obj/geo1/RBD_SOLVER", "rbdbulletsolver", fake_hou.geo)
        cache.setInput(0, solver)
        fake_hou.geo._children.extend([solver, cache])
        fake_hou._refresh_nodes()

        snapshot = commands.execute("scene-snapshot", {"path": "/obj/geo1"}, hou_module=fake_hou)

        self.assertEqual(snapshot["semantics"]["inferred_purpose"], "simulation_with_cache")
        self.assertIn("/obj/geo1/CACHE_OUT", [item["path"] for item in snapshot["semantics"]["cache_nodes"]])
        self.assertIn("/obj/geo1/RBD_SOLVER", [item["path"] for item in snapshot["semantics"]["simulation_nodes"]])
        self.assertEqual(snapshot["summary"]["cache_node_count"], 1)
        self.assertEqual(snapshot["summary"]["simulation_node_count"], 1)
        self.assertEqual(snapshot["summary"]["volume_node_count"], 0)
        self.assertEqual(snapshot["summary"]["risk_domain_count"], len(snapshot["semantics"]["risk_domains"]))
        risk_domains = {item["domain"]: item for item in snapshot["semantics"]["risk_domains"]}
        self.assertIn("cache_output", risk_domains)
        self.assertIn("simulation_settings", risk_domains)
        self.assertIn("cook_cost", risk_domains)
        self.assertIn("/obj/geo1/CACHE_OUT", risk_domains["cache_output"]["paths"])
        self.assertIn("/obj/geo1/RBD_SOLVER", risk_domains["simulation_settings"]["paths"])
        self.assertIn("houdini_node_parms", risk_domains["simulation_settings"]["suggested_tools"])
        understanding = snapshot["semantics"]["scene_understanding"]
        self.assertEqual(understanding["state"], "risk_domain_detected")
        self.assertEqual(understanding["primary_risk_domain"], "simulation_settings")
        self.assertEqual(understanding["primary_risk_priority"], "high")
        self.assertIn("houdini_node_parms", understanding["first_read_tools"])
        self.assertIn("houdini_upstream", understanding["first_read_tools"])
        self.assertFalse(understanding["may_execute"])
        self.assertFalse(understanding["safe_to_run_direct_edits"])
        self.assertTrue(any(item["template"] == "rbd-preview" for item in understanding["suggested_templates"]))
        focus_by_path = {item["path"]: item for item in snapshot["semantics"]["focus_candidates"]}
        self.assertIn("cache", focus_by_path["/obj/geo1/CACHE_OUT"]["kinds"])
        self.assertIn("simulation", focus_by_path["/obj/geo1/RBD_SOLVER"]["kinds"])
        self.assertIn("houdini_upstream", focus_by_path["/obj/geo1/RBD_SOLVER"]["mcp_tools"])
        self.assertEqual(snapshot["summary"]["risk_count"], len(snapshot["semantics"]["risk_notes"]))
        hints = snapshot["semantics"]["inspection_hints"]
        cache_hints = [item for item in hints if item.get("kind") == "cache"]
        simulation_hints = [item for item in hints if item.get("kind") == "simulation"]
        self.assertTrue(any(item["command"] == "node_parms" and item["payload"]["path"] == "/obj/geo1/CACHE_OUT" for item in cache_hints))
        self.assertTrue(any(item["command"] == "node_parms" and item["payload"]["path"] == "/obj/geo1/RBD_SOLVER" for item in simulation_hints))
        suggestions_by_template = {item["template"]: item for item in snapshot["semantics"]["workflow_suggestions"]}
        self.assertIn("rbd-preview", suggestions_by_template)
        self.assertIn("cache-output", suggestions_by_template)
        self.assertEqual(suggestions_by_template["rbd-preview"]["mcp_tool"], "houdini_template_plan")
        self.assertIn("simulation_settings", suggestions_by_template["rbd-preview"]["risk_domains"])
        self.assertIn("cache_output", suggestions_by_template["cache-output"]["risk_domains"])
        self.assertIn("snapshot_before", suggestions_by_template["cache-output"]["evidence_expectations"])
        self.assertIn("houdini_validate_plan", suggestions_by_template["cache-output"]["suggested_next_tools"])

    def test_scene_snapshot_semantics_identify_volume_nodes(self):
        fake_hou = FakeHou()
        vdb = FakeNode("/obj/geo1/VDB_FROM_POLYGONS", "vdbfrompolygons", fake_hou.geo)
        out = FakeNode("/obj/geo1/OUT_VDB", "null", fake_hou.geo)
        vdb.setInput(0, fake_hou.box)
        out.setInput(0, vdb)
        fake_hou.geo._children.extend([vdb, out])
        fake_hou._refresh_nodes()

        snapshot = commands.execute("scene-snapshot", {"path": "/obj/geo1"}, hou_module=fake_hou)

        self.assertEqual(snapshot["semantics"]["inferred_purpose"], "volume_or_vdb_setup")
        self.assertIn("/obj/geo1/VDB_FROM_POLYGONS", [item["path"] for item in snapshot["semantics"]["volume_nodes"]])
        self.assertEqual(snapshot["summary"]["volume_node_count"], 1)
        self.assertEqual(snapshot["summary"]["simulation_node_count"], 0)
        self.assertEqual(snapshot["summary"]["risk_domain_count"], len(snapshot["semantics"]["risk_domains"]))
        risk_domains = {item["domain"]: item for item in snapshot["semantics"]["risk_domains"]}
        self.assertIn("volume_resolution", risk_domains)
        self.assertIn("/obj/geo1/VDB_FROM_POLYGONS", risk_domains["volume_resolution"]["paths"])
        self.assertIn("vdb-sdf-preview", risk_domains["volume_resolution"]["workflow_templates"])
        self.assertIn("houdini_node_parms", risk_domains["volume_resolution"]["suggested_tools"])
        focus_by_path = {item["path"]: item for item in snapshot["semantics"]["focus_candidates"]}
        self.assertIn("volume", focus_by_path["/obj/geo1/VDB_FROM_POLYGONS"]["kinds"])
        self.assertIn("houdini_node_parms", focus_by_path["/obj/geo1/VDB_FROM_POLYGONS"]["mcp_tools"])
        volume_hints = [item for item in snapshot["semantics"]["inspection_hints"] if item.get("kind") == "volume"]
        self.assertTrue(any(item["command"] == "node_parms" and item["payload"]["path"] == "/obj/geo1/VDB_FROM_POLYGONS" for item in volume_hints))
        suggestions_by_template = {item["template"]: item for item in snapshot["semantics"]["workflow_suggestions"]}
        self.assertIn("vdb-sdf-preview", suggestions_by_template)
        self.assertEqual(suggestions_by_template["vdb-sdf-preview"]["mcp_tool"], "houdini_template_plan")
        self.assertIn("volume_resolution", suggestions_by_template["vdb-sdf-preview"]["risk_domains"])
        self.assertEqual(suggestions_by_template["vdb-sdf-preview"]["template_arguments"]["input"], "/obj/geo1/OUT")
        self.assertTrue(suggestions_by_template["vdb-sdf-preview"]["local_generation_only"])

    def test_readonly_graph_commands_trace_networks(self):
        fake_hou = FakeHou()
        network = commands.execute("network", {"path": "/obj/geo1"}, hou_module=fake_hou)
        self.assertEqual(network["node_count"], 3)
        self.assertIn({"src": "/obj/geo1/box1", "dst": "/obj/geo1/OUT", "input_index": 0}, network["wires"])
        self.assertIn("/obj/geo1/OUT", network["display_nodes"])

        upstream = commands.execute("upstream", {"path": "/obj/geo1/set_pscale1", "depth": 2}, hou_module=fake_hou)
        self.assertEqual(upstream["direction"], "upstream")
        self.assertEqual(upstream["node_count"], 3)
        self.assertIn({"src": "/obj/geo1/OUT", "dst": "/obj/geo1/set_pscale1", "input_index": 0}, upstream["wires"])

        downstream = commands.execute("downstream", {"path": "/obj/geo1/box1", "depth": 2}, hou_module=fake_hou)
        self.assertEqual(downstream["direction"], "downstream")
        self.assertEqual(downstream["node_count"], 3)
        self.assertIn({"src": "/obj/geo1/OUT", "dst": "/obj/geo1/set_pscale1", "input_index": 0}, downstream["wires"])

    def test_server_routes_houdini_commands_through_main_thread_dispatch(self):
        server_path = os.path.join(PYTHON_DIR, "blib_hou_bridge", "server.py")
        with open(server_path, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("commands.execute_in_houdini", source)

    def test_server_rejects_non_localhost_binding(self):
        with self.assertRaises(ValueError):
            server.BridgeServer(host="0.0.0.0")

    def test_server_requires_header_and_body_tokens_and_reports_bad_json(self):
        old_save_session = auth.save_session
        old_clear_session = auth.clear_session
        with tempfile.TemporaryDirectory() as tmpdir:
            session_path = Path(tmpdir) / "session.json"

            def fake_save_session(host, port, token, path=None, pid=None):
                session_path.write_text("{}", encoding="utf-8")
                return session_path

            def fake_clear_session(path=None):
                return True

            auth.save_session = fake_save_session
            auth.clear_session = fake_clear_session
            bridge = server.BridgeServer(token="secret")
            try:
                bridge.start()

                status_code, response = _post_rpc(
                    bridge.port,
                    protocol.make_request("health", {}, token="secret"),
                    header_token="secret",
                )
                self.assertEqual(status_code, 200)
                self.assertTrue(response["ok"])

                status_code, response = _post_rpc(
                    bridge.port,
                    protocol.make_request("health", {}),
                    header_token="secret",
                )
                self.assertEqual(status_code, 401)
                self.assertEqual(response["error"]["code"], "unauthorized")

                status_code, response = _post_rpc(
                    bridge.port,
                    protocol.make_request("health", {}, token="secret"),
                    header_token=None,
                )
                self.assertEqual(status_code, 401)
                self.assertEqual(response["error"]["code"], "unauthorized")

                status_code, response = _post_rpc(bridge.port, {}, header_token="secret", raw_body=b"{bad")
                self.assertEqual(status_code, 400)
                self.assertEqual(response["error"]["code"], "bad_request")

                log = history.snapshot()
                self.assertGreaterEqual(log["count"], 4)
                self.assertEqual(log["events"][-1]["status"], 400)
                self.assertEqual(log["events"][-1]["error_code"], "bad_request")
            finally:
                bridge.stop()
                auth.save_session = old_save_session
                auth.clear_session = old_clear_session

    def test_stop_without_local_server_does_not_clear_external_session(self):
        server_path = os.path.join(PYTHON_DIR, "blib_hou_bridge", "server.py")
        with open(server_path, "r", encoding="utf-8") as handle:
            source = handle.read()
        stop_block = source[source.index("def stop_server"):source.index("def status")]
        self.assertNotIn("auth.clear_session", stop_block)

    def test_auth_session_roundtrip_rejects_non_localhost(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "session.json"
            auth.save_session("127.0.0.1", 12345, "token", path=path, pid=42)
            loaded = auth.load_session(path)
            self.assertEqual(loaded["host"], "127.0.0.1")
            self.assertEqual(loaded["port"], 12345)

            path.write_text(json.dumps({"host": "0.0.0.0", "port": 1, "token": "x"}), encoding="utf-8")
            self.assertIsNone(auth.load_session(path))

    def test_cli_reports_offline_without_touching_houdini(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "missing.json")
            self.assertEqual(blib_hou.main(["--session", path, "status"]), 2)

    def test_cli_maps_status_to_health_protocol_command(self):
        self.assertEqual(blib_hou._protocol_command("status"), "health")
        self.assertEqual(blib_hou._protocol_command("find-nodes"), "find_nodes")
        self.assertEqual(blib_hou._protocol_command("manifest"), "manifest")
        self.assertEqual(blib_hou._protocol_command("profile-manifest"), "profile_manifest")
        self.assertEqual(blib_hou._protocol_command("probe-parm-profile"), "probe_parm_profile")
        self.assertEqual(blib_hou._protocol_command("node-parms"), "node_parms")
        self.assertEqual(blib_hou._protocol_command("rpc-log"), "rpc_log")
        self.assertEqual(blib_hou._protocol_command("node-info"), "node_info")
        self.assertEqual(blib_hou._protocol_command("scene-snapshot"), "scene_snapshot")
        self.assertEqual(blib_hou._protocol_command("validate-plan"), "validate_plan")
        self.assertEqual(blib_hou._protocol_command("recipe-manifest"), "recipe_manifest")
        self.assertEqual(blib_hou._protocol_command("review-plan"), "review_plan")
        self.assertEqual(blib_hou._protocol_command("verify-plan"), "verify_plan")
        self.assertEqual(blib_hou._protocol_command("viewport-screenshot"), "viewport_screenshot")

    def test_cli_doctor_reports_missing_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "missing.json")
            report = blib_hou._doctor(path)
            self.assertFalse(report["ok"])
            self.assertEqual(report["error"]["code"], "offline")
            self.assertEqual(report["checks"][0]["name"], "session_file")
            self.assertFalse(report["checks"][0]["ok"])

    def test_cli_doctor_checks_rpc_health_without_leaking_token(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "session.json")
            auth.save_session("127.0.0.1", 12345, "secret", path=path, pid=42)
            calls = []
            old_post = blib_hou._post
            try:
                blib_hou._post = lambda host, port, payload, token: calls.append(
                    {"host": host, "port": port, "payload": payload, "token": token}
                ) or {"ok": True, "result": {"status": "ok"}}
                report = blib_hou._doctor(path)
            finally:
                blib_hou._post = old_post

            self.assertTrue(report["ok"])
            self.assertEqual(report["session"]["host"], "127.0.0.1")
            self.assertEqual(report["session"]["port"], 12345)
            self.assertTrue(report["session"]["token_present"])
            self.assertNotIn("secret", json.dumps(report))
            self.assertEqual(calls[0]["payload"]["command"], "health")
            self.assertEqual(calls[0]["payload"]["token"], "secret")
            self.assertEqual(calls[0]["token"], "secret")

    def test_protocol_validates_readonly_graph_payloads(self):
        self.assertIn("network", protocol.READ_COMMANDS)
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.make_request("network", {"path": "obj/geo1"})
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.make_request("find-nodes", {"root": "obj"})
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.make_request("find-nodes", {"root": "/obj", "limit": 999})
        request = protocol.make_request("find-nodes", {"root": "/obj", "type": "geo", "limit": 2})
        self.assertEqual(request["command"], "find_nodes")
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.make_request("node-parms", {"path": "obj/geo1"})
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.make_request("rpc-log", {"limit": 999})
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.make_request("upstream", {"path": "/obj/geo1/OUT", "depth": 99})
        request = protocol.make_request("downstream", {"path": "/obj/geo1/OUT", "depth": 2})
        self.assertEqual(request["command"], "downstream")
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.make_request("scene-snapshot", {"path": "obj/geo1"})
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.make_request("scene-snapshot", {"trace_depth": 99})
        request = protocol.make_request(
            "validate-plan",
            {"steps": [{"command": "context", "payload": {}}]},
        )
        self.assertEqual(request["command"], "validate_plan")
        request = protocol.make_request(
            "review-plan",
            {"steps": [{"command": "context", "payload": {}}]},
        )
        self.assertEqual(request["command"], "review_plan")
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.make_request("validate-plan", {"steps": [{"payload": {}}]})
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.make_request("review-plan", {"steps": [{"payload": {}}]})
        long_steps = [{"command": "context", "payload": {}} for _ in range(101)]
        self.assertEqual(protocol.make_request("validate-plan", {"steps": long_steps})["command"], "validate_plan")
        self.assertEqual(protocol.make_request("review-plan", {"steps": long_steps})["command"], "review_plan")
        request = protocol.make_request(
            "verify-plan",
            {"steps": [{"command": "context", "payload": {}}], "validation": {"steps": []}, "run_result": {"ok": True}},
        )
        self.assertEqual(request["command"], "verify_plan")
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.make_request("verify-plan", {"steps": [], "validation": []})
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.make_request("verify-plan", {"steps": [], "run_result": []})
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.make_request("set-node-color", {"node": "/obj/geo1", "color": [2, 0, 0]})
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.make_request("rename-node", {"node": "/obj/geo1", "name": "bad-name"})
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.make_request("create-network-box", {"parent": "/obj/geo1", "name": "BOX", "nodes": ["relative"]})
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.make_request("create-sticky-note", {"parent": "/obj/geo1", "text": "note", "x": 1})
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.make_request("set-parm-any", {"node": "/obj/geo1", "parms": [], "value": 1})
        request = protocol.make_request("set-parm-any", {"node": "/obj/geo1", "parms": ["file", "sopoutput"], "value": "$HIP/test.bgeo.sc"})
        self.assertEqual(request["command"], "set_parm_any")
        request = protocol.make_request("batch-set-parms", {"node": "/obj/geo1", "values": {"tx": 2}, "required": False})
        self.assertEqual(request["command"], "batch_set_parms")
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.make_request("batch-set-parms", {"node": "/obj/geo1", "values": {}})
        request = protocol.make_request("set-input", {"dst": "/obj/geo1/OUT", "input_index": 0, "clear": True})
        self.assertEqual(request["command"], "set_input")
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.make_request("set-input", {"dst": "/obj/geo1/OUT", "input_index": 0, "src": "/obj/geo1/box1", "clear": True})
        request = protocol.make_request("disconnect", {"node": "/obj/geo1/OUT", "input_index": 0})
        self.assertEqual(request["command"], "disconnect")
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.make_request("disconnect", {"node": "/obj/geo1/OUT"})
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.make_request("disconnect", {"node": "/obj/geo1/OUT", "all": True, "src": "/obj/geo1/box1"})
        request = protocol.make_request("copy-node", {"node": "/obj/geo1/OUT", "parent": "/obj/geo1", "name": "OUT_COPY"})
        self.assertEqual(request["command"], "copy_node")
        request = protocol.make_request("move-node", {"node": "/obj/geo1/OUT", "parent": "/obj"})
        self.assertEqual(request["command"], "move_node")
        request = protocol.make_request("set-node-shape", {"node": "/obj/geo1/OUT", "shape": "bone"})
        self.assertEqual(request["command"], "set_node_shape")
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.make_request("replace-node", {"node": "/obj/geo1/OUT", "type": "null", "delete_old": True})
        request = protocol.make_request("replace-node", {"node": "/obj/geo1/OUT", "type": "null", "delete_old": True, "confirm": True})
        self.assertEqual(request["command"], "replace_node")
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.make_request("delete-node", {"node": "/obj/geo1/OUT"})
        request = protocol.make_request("delete-node", {"node": "/obj/geo1/OUT", "confirm": True})
        self.assertEqual(request["command"], "delete_node")
        request = protocol.make_request(
            "apply-parm-profile",
            {"node": "/obj/geo1", "profile": "rbd-fracture-preview", "values": {"detail_size": 0.1}},
        )
        self.assertEqual(request["command"], "apply_parm_profile")
        request = protocol.make_request(
            "probe-parm-profile",
            {"node": "/obj/geo1", "profile": "rbd-fracture-preview", "values": {"detail_size": 0.1}},
        )
        self.assertEqual(request["command"], "probe_parm_profile")
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.make_request("probe-parm-profile", {"node": "/obj/geo1", "profile": "rbd-fracture-preview", "values": []})
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.make_request("run-plan", {"steps": [{"command": "run-plan", "payload": {"steps": []}}]})
        request = protocol.make_request("run-plan", {"steps": [{"command": "context", "payload": {}}]})
        self.assertEqual(request["command"], "run_plan")

    def test_viewport_screenshot_is_readonly_and_restricted_to_temp_output(self):
        self.assertIn("viewport_screenshot", protocol.READ_COMMANDS)
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.make_request("viewport-screenshot", {"width": 12})
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.make_request("viewport-screenshot", {"prefix": "../bad"})

        result = commands.execute(
            "viewport-screenshot",
            {"width": 640, "height": 480, "prefix": "unit_test"},
            hou_module=FakeHou(),
        )
        self.assertTrue(os.path.exists(result["path"]))
        self.assertIn(os.path.join("blib_hou_bridge", "screenshots"), result["path"])
        self.assertEqual(result["width"], 640)
        self.assertEqual(result["height"], 480)

    def test_edit_commands_require_edit_mode(self):
        fake_hou = FakeHou()
        with self.assertRaises(commands.BridgeCommandError):
            commands.execute("create-node", {"parent": "/obj/geo1", "type": "null", "name": "OUT_TEST"}, hou_module=fake_hou)

    def test_validate_plan_checks_steps_without_scene_edits(self):
        fake_hou = FakeHou()
        steps = [
            {"command": "create-node", "payload": {"parent": "/obj/geo1", "type": "null", "name": "PREVIEW"}},
            {"command": "layout", "payload": {"path": "/obj/geo1"}},
            {"command": "create-network-box", "payload": {"parent": "/obj/geo1", "name": "BOX_PREVIEW", "nodes": ["/obj/geo1/OUT"]}},
            {"command": "set-parm", "payload": {"node": "/obj/geo1/OUT", "parm": "missing", "value": 1}},
            {"command": "run-python", "payload": {}},
        ]
        report = commands.execute(
            "validate-plan",
            {"steps": steps},
            hou_module=fake_hou,
        )
        expected_sha = hashlib.sha256(json.dumps(steps, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        self.assertFalse(report["valid"])
        self.assertFalse(report["ready_to_run"])
        self.assertEqual(report["step_count"], len(steps))
        self.assertEqual(report["steps_sha256"], expected_sha)
        self.assertTrue(report["would_require_edit"])
        self.assertTrue(report["blocked_by_edit_mode"])
        self.assertTrue(report["steps"][0]["valid"])
        self.assertEqual(report["steps"][0]["creates"], ["/obj/geo1/PREVIEW"])
        self.assertEqual(report["steps"][2]["creates"], ["/obj/geo1/BOX_PREVIEW"])
        self.assertIn("Parameter not found", report["steps"][3]["issues"][0])
        self.assertIn("Danger commands", report["steps"][4]["issues"][0])
        self.assertIsNone(fake_hou.node("/obj/geo1/PREVIEW"))

    def test_review_plan_carries_validation_steps_fingerprint(self):
        fake_hou = FakeHou()
        steps = [{"command": "context", "payload": {}}]
        expected_sha = hashlib.sha256(json.dumps(steps, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

        review = commands.execute("review-plan", {"steps": steps}, hou_module=fake_hou)

        self.assertEqual(review["validation"]["step_count"], 1)
        self.assertEqual(review["validation"]["steps_sha256"], expected_sha)

    def test_validate_plan_requires_network_box_after_layout(self):
        fake_hou = FakeHou()
        report = commands.execute(
            "validate-plan",
            {
                "steps": [
                    {"command": "create-network-box", "payload": {"parent": "/obj/geo1", "name": "BOX_PREVIEW", "nodes": ["/obj/geo1/OUT"]}},
                    {"command": "layout", "payload": {"path": "/obj/geo1"}},
                ]
            },
            hou_module=fake_hou,
        )
        self.assertFalse(report["valid"])
        self.assertIn("after a layout step", report["steps"][0]["issues"][0])

    def test_validate_plan_allows_references_to_planned_paths(self):
        fake_hou = FakeHou()
        report = commands.execute(
            "validate-plan",
            {
                "steps": [
                    {"command": "create-node", "payload": {"parent": "/obj/geo1", "type": "null", "name": "PREVIEW"}},
                    {"command": "set-parm", "payload": {"node": "/obj/geo1/PREVIEW", "parm": "tx", "value": 2}},
                    {"command": "connect", "payload": {"src": "/obj/geo1/OUT", "dst": "/obj/geo1/PREVIEW"}},
                    {"command": "rename-node", "payload": {"node": "/obj/geo1/PREVIEW", "name": "OUT_PREVIEW"}},
                    {"command": "select", "payload": {"path": "/obj/geo1/OUT_PREVIEW"}},
                ]
            },
            hou_module=fake_hou,
        )
        self.assertTrue(report["valid"])
        self.assertTrue(report["would_require_edit"])
        self.assertEqual(report["steps"][0]["creates"], ["/obj/geo1/PREVIEW"])
        self.assertIn("Parameter existence cannot be checked", report["steps"][1]["warnings"][0])
        self.assertFalse(report["steps"][2]["issues"])
        self.assertEqual(report["steps"][3]["aliases"]["/obj/geo1/PREVIEW"], "/obj/geo1/OUT_PREVIEW")
        self.assertFalse(report["steps"][4]["issues"])
        self.assertIsNone(fake_hou.node("/obj/geo1/PREVIEW"))

    def test_validate_plan_understands_set_parm_any_on_existing_and_planned_nodes(self):
        fake_hou = FakeHou()
        report = commands.execute(
            "validate-plan",
            {
                "steps": [
                    {"command": "set-parm-any", "payload": {"node": "/obj/geo1/OUT", "parms": ["missing", "tx"], "value": 2, "required": True}},
                    {"command": "set-parm-any", "payload": {"node": "/obj/geo1/OUT", "parms": ["missing"], "value": 2, "required": False}},
                    {"command": "create-node", "payload": {"parent": "/obj/geo1", "type": "filecache", "name": "CACHE_TEST"}},
                    {"command": "set-parm-any", "payload": {"node": "/obj/geo1/CACHE_TEST", "parms": ["file", "sopoutput"], "value": "$HIP/cache/test.bgeo.sc"}},
                ]
            },
            hou_module=fake_hou,
        )
        self.assertTrue(report["steps"][0]["valid"])
        self.assertTrue(report["steps"][1]["valid"])
        self.assertIn("No candidate parameter currently exists", report["steps"][1]["warnings"][0])
        self.assertTrue(report["steps"][3]["valid"])
        self.assertIn("Parameter candidates cannot be checked on planned node", report["steps"][3]["warnings"][0])

    def test_validate_plan_understands_apply_parm_profile(self):
        fake_hou = FakeHou()
        report = commands.execute(
            "validate-plan",
            {
                "steps": [
                    {"command": "create-node", "payload": {"parent": "/obj/geo1", "type": "rbdmaterialfracture", "name": "FRACTURE_TEST"}},
                    {
                        "command": "apply-parm-profile",
                        "payload": {"node": "/obj/geo1/FRACTURE_TEST", "profile": "rbd-fracture-preview", "values": {"detail_size": 0.1}},
                    },
                    {
                        "command": "apply-parm-profile",
                        "payload": {"node": "/obj/geo1/OUT", "profile": "rbd-fracture-preview", "values": {}, "strict": False},
                    },
                ]
            },
            hou_module=fake_hou,
        )
        self.assertTrue(report["steps"][1]["valid"])
        self.assertIn("Profile parameters cannot be checked on planned node", report["steps"][1]["warnings"][0])
        self.assertTrue(report["steps"][2]["valid"])
        self.assertIn("No profile parameter candidates currently exist", report["steps"][2]["warnings"][0])

    def test_validate_plan_understands_probe_parm_profile(self):
        fake_hou = FakeHou()
        report = commands.execute(
            "validate-plan",
            {
                "steps": [
                    {"command": "probe-parm-profile", "payload": {"node": "/obj/geo1/OUT", "profile": "rbd-fracture-preview", "values": {}}},
                    {"command": "probe-parm-profile", "payload": {"node": "/obj/geo1/OUT", "profile": "missing-profile", "values": {}}},
                ]
            },
            hou_module=fake_hou,
        )
        self.assertTrue(report["steps"][0]["valid"])
        self.assertEqual(report["steps"][0]["permission"], "read")
        self.assertIn("Some profile parameters would be skipped", report["steps"][0]["warnings"][0])
        self.assertFalse(report["steps"][1]["valid"])
        self.assertIn("Unknown parameter profile", report["steps"][1]["issues"][0])

    def test_review_plan_reports_impact_and_tuning_suggestions(self):
        fake_hou = FakeHou()
        review = commands.execute(
            "review-plan",
            {
                "steps": [
                    {"command": "create-node", "payload": {"parent": "/obj/geo1", "type": "clean", "name": "clean1"}},
                    {"command": "create-node", "payload": {"parent": "/obj/geo1", "type": "fuse", "name": "fuse1"}},
                    {"command": "connect", "payload": {"src": "/obj/geo1/clean1", "dst": "/obj/geo1/fuse1"}},
                ]
            },
            hou_module=fake_hou,
        )
        self.assertEqual(review["level"], "blocked")
        self.assertIn("/obj/geo1/clean1", review["impact"]["created"])
        self.assertIn("/obj/geo1/fuse1", review["impact"]["created"])
        self.assertIn("Plan contains edit commands while bridge edit mode is off.", review["blockers"])
        self.assertIn("Plan creates or edits multiple nodes but has no layout step.", review["warnings"])
        self.assertIn("Add a layout step", review["suggestions"][0])
        self.assertFalse(any("create_network_box" in suggestion for suggestion in review["suggestions"]))
        self.assertIn("build_sop_cleanup_setup", [hint["recipe"] for hint in review["recipe_hints"]])

    def test_review_plan_reports_cache_simulation_and_render_risk_notes(self):
        fake_hou = FakeHou()
        plan = [
            {"command": "create-node", "payload": {"parent": "/obj/geo1", "type": "filecache", "name": "CACHE_TEST"}},
            {"command": "set-parm-any", "payload": {"node": "/obj/geo1/CACHE_TEST", "parms": ["file", "sopoutput"], "value": "$HIP/cache/test.$F4.bgeo.sc"}},
            {"command": "create-node", "payload": {"parent": "/obj/geo1", "type": "rbdbulletsolver", "name": "SIM_SOLVER"}},
            {"command": "create-node", "payload": {"parent": "/obj", "type": "lopnet", "name": "SHOT_STAGE"}},
            {"command": "create-node", "payload": {"parent": "/obj/SHOT_STAGE", "type": "usdrender_rop", "name": "USD_RENDER_ROP"}},
            {"command": "layout", "payload": {"path": "/obj/geo1"}},
            {"command": "select", "payload": {"path": "/obj/SHOT_STAGE/USD_RENDER_ROP"}},
        ]

        review = commands.execute("review-plan", {"steps": plan}, hou_module=fake_hou)

        kinds = [item["kind"] for item in review["risk_notes"]]
        self.assertIn("cache", kinds)
        self.assertIn("simulation", kinds)
        self.assertIn("render", kinds)
        self.assertIn("Plan touches cache/export setup", " ".join(review["warnings"]))
        self.assertIn("Plan touches simulation/solver setup", " ".join(review["warnings"]))
        self.assertIn("Plan touches render/Solaris setup", " ".join(review["warnings"]))
        self.assertTrue(any("creates a cache node" in item for item in review["required_confirmations"]))
        self.assertTrue(any("creates a simulation/solver node" in item for item in review["required_confirmations"]))
        self.assertTrue(any("creates a render/Solaris node" in item for item in review["required_confirmations"]))
        render_notes = [item for item in review["risk_notes"] if item["kind"] == "render"]
        self.assertTrue(any("verify camera" in item["message"].lower() for item in render_notes))
        self.assertTrue(any("/obj/SHOT_STAGE/USD_RENDER_ROP" in item["touched"] for item in render_notes))

    def test_review_plan_reports_karma_template_render_risk_notes(self):
        fake_hou = FakeHou()
        plan = workflow_templates.build_plan(
            "karma-solaris-preview",
            "/obj/geo1/OUT",
            {"name": "shot", "render_path": "$HIP/render/shot/beauty.$F4.exr"},
        )

        review = commands.execute("review-plan", {"steps": plan}, hou_module=fake_hou)

        self.assertIn("render", [item["kind"] for item in review["risk_notes"]])
        self.assertTrue(any("render/Solaris" in warning for warning in review["warnings"]))
        self.assertTrue(any("verify render nodes" in suggestion for suggestion in review["suggestions"]))

    def test_validate_plan_understands_cleanup_edit_commands(self):
        fake_hou = FakeHou()
        steps = [
            {"command": "batch-set-parms", "payload": {"node": "/obj/geo1/OUT", "values": {"tx": 2}, "required": True}},
            {"command": "set-input", "payload": {"dst": "/obj/geo1/set_pscale1", "input_index": 0, "src": "/obj/geo1/box1"}},
            {"command": "disconnect", "payload": {"node": "/obj/geo1/OUT", "input_index": 0}},
            {"command": "copy-node", "payload": {"node": "/obj/geo1/OUT", "parent": "/obj/geo1", "name": "OUT_COPY"}},
            {"command": "set-node-shape", "payload": {"node": "/obj/geo1/OUT", "shape": "bone"}},
            {
                "command": "replace-node",
                "payload": {"node": "/obj/geo1/OUT", "type": "null", "name": "OUT_REPLACED", "delete_old": True, "confirm": True},
            },
            {"command": "delete-node", "payload": {"node": "/obj/geo1/OUT_COPY", "confirm": True}},
        ]
        report = commands.execute("validate-plan", {"steps": steps}, hou_module=fake_hou)

        self.assertTrue(report["valid"])
        self.assertFalse(report["ready_to_run"])
        self.assertTrue(report["would_require_edit"])
        self.assertTrue(report["blocked_by_edit_mode"])
        self.assertEqual(report["steps"][0]["touches"], ["/obj/geo1/OUT"])
        self.assertEqual(report["steps"][3]["creates"], ["/obj/geo1/OUT_COPY"])
        self.assertEqual(report["steps"][5]["creates"], ["/obj/geo1/OUT_REPLACED"])
        self.assertEqual(report["steps"][5]["deletes"], ["/obj/geo1/OUT"])
        self.assertEqual(report["steps"][5]["aliases"]["/obj/geo1/OUT"], "/obj/geo1/OUT_REPLACED")
        self.assertEqual(report["steps"][6]["deletes"], ["/obj/geo1/OUT_COPY"])
        self.assertIsNone(fake_hou.node("/obj/geo1/OUT_COPY"))
        self.assertIsNotNone(fake_hou.node("/obj/geo1/OUT"))

        review = commands.execute("review-plan", {"steps": steps}, hou_module=fake_hou)
        self.assertIn("/obj/geo1/OUT", review["impact"]["deleted"])
        self.assertIn("/obj/geo1/OUT_COPY", review["impact"]["deleted"])
        self.assertIn("Plan deletes nodes; verify cleanup targets before running.", review["warnings"])
        self.assertTrue(any("deletes `/obj/geo1/OUT_COPY`" in item for item in review["required_confirmations"]))
        self.assertTrue(any("replaces and deletes `/obj/geo1/OUT`" in item for item in review["required_confirmations"]))
        self.assertIn("restore_parameters", [hint["kind"] for hint in review["rollback_hints"]])
        self.assertIn("restore_wiring", [hint["kind"] for hint in review["rollback_hints"]])
        self.assertIn("destructive_delete", [hint["kind"] for hint in review["rollback_hints"]])
        self.assertIn("/obj/geo1/OUT_COPY", [hint["path"] for hint in review["rollback_hints"]])

        blocked = commands.execute(
            "validate-plan",
            {"steps": [{"command": "delete-node", "payload": {"node": "/obj/geo1", "confirm": True}}]},
            hou_module=fake_hou,
        )
        self.assertFalse(blocked["valid"])
        self.assertIn("without delete_contents=true", blocked["steps"][0]["issues"][0])

        auto_replace = commands.execute(
            "validate-plan",
            {"steps": [{"command": "replace-node", "payload": {"node": "/obj/geo1/OUT", "type": "null"}}]},
            hou_module=fake_hou,
        )
        self.assertEqual(auto_replace["steps"][0]["creates"], ["/obj/geo1/<auto null>"])

    def test_safe_edit_commands_run_when_enabled(self):
        fake_hou = FakeHou()
        state.set_edit_enabled(True)
        created = commands.execute(
            "create-node",
            {"parent": "/obj/geo1", "type": "null", "name": "OUT_TEST"},
            hou_module=fake_hou,
        )
        self.assertEqual(created["created"]["path"], "/obj/geo1/OUT_TEST")

        set_result = commands.execute("set-parm", {"node": "/obj/geo1/OUT_TEST", "parm": "tx", "value": 2}, hou_module=fake_hou)
        self.assertEqual(set_result["parm"], "tx")
        self.assertEqual(fake_hou.node("/obj/geo1/OUT_TEST").parm("tx").value, 2)

        set_any_result = commands.execute(
            "set-parm-any",
            {"node": "/obj/geo1/OUT_TEST", "parms": ["missing", "tx"], "value": 3, "required": True},
            hou_module=fake_hou,
        )
        self.assertEqual(set_any_result["parm"], "tx")
        self.assertEqual(fake_hou.node("/obj/geo1/OUT_TEST").parm("tx").value, 3)
        skipped_result = commands.execute(
            "set-parm-any",
            {"node": "/obj/geo1/OUT_TEST", "parms": ["missing"], "value": 4, "required": False},
            hou_module=fake_hou,
        )
        self.assertTrue(skipped_result["skipped"])
        with self.assertRaises(commands.BridgeCommandError):
            commands.execute(
                "set-parm-any",
                {"node": "/obj/geo1/OUT_TEST", "parms": ["missing"], "value": 4, "required": True},
                hou_module=fake_hou,
            )

        comment_result = commands.execute(
            "set-comment",
            {"node": "/obj/geo1/OUT_TEST", "comment": "Bridge comment"},
            hou_module=fake_hou,
        )
        self.assertEqual(comment_result["comment"], "Bridge comment")
        self.assertEqual(fake_hou.node("/obj/geo1/OUT_TEST").comment(), "Bridge comment")

        flag_result = commands.execute(
            "set-flags",
            {"node": "/obj/geo1/OUT_TEST", "display": True, "render": True},
            hou_module=fake_hou,
        )
        self.assertTrue(flag_result["display"])
        self.assertTrue(flag_result["render"])

        position_result = commands.execute(
            "set-position",
            {"node": "/obj/geo1/OUT_TEST", "x": 2.5, "y": -1.0},
            hou_module=fake_hou,
        )
        self.assertEqual(position_result["position"], [2.5, -1.0])
        self.assertEqual(fake_hou.node("/obj/geo1/OUT_TEST")._position, [2.5, -1.0])

        parm_result = commands.execute(
            "ensure-parm",
            {"node": "/obj/geo1/OUT_TEST", "name": "mosaic_seed", "type": "int", "label": "Seed", "default": 4},
            hou_module=fake_hou,
        )
        self.assertTrue(parm_result["created"])
        self.assertIsNotNone(fake_hou.node("/obj/geo1/OUT_TEST").parm("mosaic_seed"))

        color_result = commands.execute(
            "set-node-color",
            {"node": "/obj/geo1/OUT_TEST", "color": [0.2, 0.4, 0.8]},
            hou_module=fake_hou,
        )
        self.assertEqual(color_result["color"], [0.2, 0.4, 0.8])
        self.assertEqual(fake_hou.node("/obj/geo1/OUT_TEST")._color, (0.2, 0.4, 0.8))

        bypass_result = commands.execute(
            "bypass-node",
            {"node": "/obj/geo1/OUT_TEST", "bypass": True},
            hou_module=fake_hou,
        )
        self.assertTrue(bypass_result["bypass"])
        self.assertTrue(fake_hou.node("/obj/geo1/OUT_TEST").isBypassed())

        info = commands.execute("node-info", {"path": "/obj/geo1/OUT_TEST"}, hou_module=fake_hou)
        self.assertEqual(info["comment"], "Bridge comment")
        self.assertTrue(info["flags"]["display"])
        self.assertTrue(info["flags"]["render"])
        self.assertTrue(info["flags"]["bypass"])
        self.assertEqual(info["position"], [2.5, -1.0])
        self.assertEqual(info["color"], [0.2, 0.4, 0.8])

        connect_result = commands.execute(
            "connect",
            {"src": "/obj/geo1/OUT", "dst": "/obj/geo1/OUT_TEST", "input_index": 0},
            hou_module=fake_hou,
        )
        self.assertEqual(connect_result["src"], "/obj/geo1/OUT")

        box_result = commands.execute(
            "create-network-box",
            {
                "parent": "/obj/geo1",
                "name": "BOX_TEST",
                "comment": "Bridge box",
                "nodes": ["/obj/geo1/OUT_TEST"],
                "color": [0.1, 0.2, 0.3],
            },
            hou_module=fake_hou,
        )
        self.assertEqual(box_result["created"]["path"], "/obj/geo1/BOX_TEST")
        self.assertEqual(fake_hou.geo._network_boxes[0]._comment, "Bridge box")
        self.assertTrue(fake_hou.geo._network_boxes[0].fit_called)

        note_result = commands.execute(
            "create-sticky-note",
            {
                "parent": "/obj/geo1",
                "text": "Bridge note",
                "name": "NOTE_TEST",
                "x": 1.0,
                "y": 2.0,
                "color": [0.8, 0.7, 0.2],
            },
            hou_module=fake_hou,
        )
        self.assertEqual(note_result["created"]["path"], "/obj/geo1/NOTE_TEST")
        self.assertEqual(fake_hou.geo._sticky_notes[0]._text, "Bridge note")
        self.assertEqual(fake_hou.geo._sticky_notes[0]._position, [1.0, 2.0])

        rename_result = commands.execute(
            "rename-node",
            {"node": "/obj/geo1/OUT_TEST", "name": "OUT_RENAMED", "unique": True},
            hou_module=fake_hou,
        )
        self.assertEqual(rename_result["old_path"], "/obj/geo1/OUT_TEST")
        self.assertEqual(rename_result["renamed"]["path"], "/obj/geo1/OUT_RENAMED")

        layout_result = commands.execute("layout", {"path": "/obj/geo1"}, hou_module=fake_hou)
        self.assertEqual(layout_result["touched"], ["/obj/geo1"])
        self.assertTrue(fake_hou.geo._layout_called)

        select_result = commands.execute("select", {"path": "/obj/geo1/OUT_RENAMED"}, hou_module=fake_hou)
        self.assertEqual(select_result["selected"]["path"], "/obj/geo1/OUT_RENAMED")
        self.assertTrue(fake_hou.node("/obj/geo1/OUT_RENAMED").isSelected())

    def test_cleanup_edit_commands_run_when_enabled(self):
        fake_hou = FakeHou()
        state.set_edit_enabled(True)

        batch = commands.execute(
            "batch-set-parms",
            {"node": "/obj/geo1/OUT", "values": {"tx": 8, "missing": 4}, "required": False},
            hou_module=fake_hou,
        )
        self.assertEqual(batch["applied"], [{"parm": "tx", "value": 8}])
        self.assertEqual(batch["missing"], ["missing"])
        self.assertEqual(fake_hou.node("/obj/geo1/OUT").parm("tx").value, 8)

        rewired = commands.execute(
            "set-input",
            {"dst": "/obj/geo1/set_pscale1", "input_index": 0, "src": "/obj/geo1/box1"},
            hou_module=fake_hou,
        )
        self.assertEqual(rewired["previous_src"], "/obj/geo1/OUT")
        self.assertIs(fake_hou.wrangle.inputs()[0], fake_hou.box)
        self.assertNotIn(fake_hou.wrangle, fake_hou.out.outputs())

        disconnected = commands.execute(
            "disconnect",
            {"node": "/obj/geo1/set_pscale1", "src": "/obj/geo1/box1"},
            hou_module=fake_hou,
        )
        self.assertEqual(disconnected["count"], 1)
        self.assertIsNone(fake_hou.wrangle.inputs()[0])

        restored = commands.execute(
            "set-input",
            {"dst": "/obj/geo1/set_pscale1", "input_index": 0, "src": "/obj/geo1/OUT"},
            hou_module=fake_hou,
        )
        self.assertEqual(restored["src"], "/obj/geo1/OUT")
        self.assertIs(fake_hou.wrangle.inputs()[0], fake_hou.out)

        copied = commands.execute(
            "copy-node",
            {"node": "/obj/geo1/OUT", "parent": "/obj/geo1", "name": "OUT_COPY"},
            hou_module=fake_hou,
        )
        self.assertEqual(copied["created"]["path"], "/obj/geo1/OUT_COPY")
        self.assertIsNotNone(fake_hou.node("/obj/geo1/OUT_COPY"))

        shaped = commands.execute(
            "set-node-shape",
            {"node": "/obj/geo1/OUT_COPY", "shape": "bone"},
            hou_module=fake_hou,
        )
        self.assertEqual(shaped["shape"], "bone")
        self.assertEqual(fake_hou.node("/obj/geo1/OUT_COPY").userData("nodeshape"), "bone")

        replacement = commands.execute(
            "replace-node",
            {"node": "/obj/geo1/OUT", "type": "null", "name": "OUT_REPLACED", "delete_old": True, "confirm": True},
            hou_module=fake_hou,
        )
        self.assertEqual(replacement["deleted_old"], "/obj/geo1/OUT")
        self.assertEqual(replacement["replacement"]["path"], "/obj/geo1/OUT_REPLACED")
        self.assertIsNone(fake_hou.node("/obj/geo1/OUT"))
        new_out = fake_hou.node("/obj/geo1/OUT_REPLACED")
        self.assertIsNotNone(new_out)
        self.assertIs(new_out.inputs()[0], fake_hou.box)
        self.assertIs(fake_hou.wrangle.inputs()[0], new_out)

        deleted = commands.execute(
            "delete-node",
            {"node": "/obj/geo1/OUT_COPY", "confirm": True},
            hou_module=fake_hou,
        )
        self.assertEqual(deleted["deleted"], "/obj/geo1/OUT_COPY")
        self.assertIsNone(fake_hou.node("/obj/geo1/OUT_COPY"))

        moved_source = commands.execute(
            "create-node",
            {"parent": "/obj/geo1", "type": "null", "name": "MOVE_ME"},
            hou_module=fake_hou,
        )
        self.assertEqual(moved_source["created"]["path"], "/obj/geo1/MOVE_ME")
        moved = commands.execute(
            "move-node",
            {"node": "/obj/geo1/MOVE_ME", "parent": "/obj", "name": "MOVED_TEST"},
            hou_module=fake_hou,
        )
        self.assertEqual(moved["old_path"], "/obj/geo1/MOVE_ME")
        self.assertEqual(moved["moved"]["path"], "/obj/MOVED_TEST")
        self.assertIsNone(fake_hou.node("/obj/geo1/MOVE_ME"))
        self.assertIsNotNone(fake_hou.node("/obj/MOVED_TEST"))

        nested_parent = commands.execute(
            "create-node",
            {"parent": "/obj/geo1", "type": "subnet", "name": "TMP_PARENT"},
            hou_module=fake_hou,
        )
        fake_hou.node(nested_parent["created"]["path"]).createNode("null", "TMP_CHILD")
        with self.assertRaises(commands.BridgeCommandError):
            commands.execute(
                "delete-node",
                {"node": "/obj/geo1/TMP_PARENT", "confirm": True},
                hou_module=fake_hou,
            )
        commands.execute(
            "delete-node",
            {"node": "/obj/geo1/TMP_PARENT", "confirm": True, "delete_contents": True},
            hou_module=fake_hou,
        )
        self.assertIsNone(fake_hou.node("/obj/geo1/TMP_PARENT"))
        self.assertIsNone(fake_hou.node("/obj/geo1/TMP_PARENT/TMP_CHILD"))

    def test_apply_parm_profile_matches_optional_strict_and_clamps(self):
        fake_hou = FakeHou()
        state.set_edit_enabled(True)
        fracture = commands.execute(
            "create-node",
            {"parent": "/obj/geo1", "type": "rbdmaterialfracture", "name": "FRACTURE_PROFILE"},
            hou_module=fake_hou,
        )
        self.assertEqual(fracture["created"]["path"], "/obj/geo1/FRACTURE_PROFILE")
        result = commands.execute(
            "apply-parm-profile",
            {
                "node": "/obj/geo1/FRACTURE_PROFILE",
                "profile": "rbd-fracture-preview",
                "values": {"detail_size": 999},
                "strict": False,
            },
            hou_module=fake_hou,
        )
        self.assertEqual(result["applied"][0]["parm"], "fracturelevel")
        self.assertEqual(fake_hou.node("/obj/geo1/FRACTURE_PROFILE").parm("fracturelevel").value, 10.0)
        self.assertTrue(result["clamped"])

        optional = commands.execute(
            "apply-parm-profile",
            {"node": "/obj/geo1/OUT", "profile": "rbd-fracture-preview", "values": {}, "strict": False},
            hou_module=fake_hou,
        )
        self.assertFalse(optional["applied"])
        self.assertTrue(optional["skipped"])
        with self.assertRaises(commands.BridgeCommandError):
            commands.execute(
                "apply-parm-profile",
                {"node": "/obj/geo1/OUT", "profile": "rbd-fracture-preview", "values": {}, "strict": True},
                hou_module=fake_hou,
            )

    def test_probe_parm_profile_is_readonly_and_matches_apply_resolution(self):
        fake_hou = FakeHou()
        state.set_edit_enabled(True)
        commands.execute(
            "create-node",
            {"parent": "/obj/geo1", "type": "rbdmaterialfracture", "name": "FRACTURE_PROBE"},
            hou_module=fake_hou,
        )
        node = fake_hou.node("/obj/geo1/FRACTURE_PROBE")
        before = node.parm("fracturelevel").value
        probe = commands.execute(
            "probe-parm-profile",
            {"node": "/obj/geo1/FRACTURE_PROBE", "profile": "rbd-fracture-preview", "values": {"detail_size": 999}, "strict": False},
            hou_module=fake_hou,
        )
        self.assertEqual(node.parm("fracturelevel").value, before)
        self.assertEqual(probe["matched"][0]["parm"], "fracturelevel")
        self.assertTrue(probe["clamped"])
        self.assertIn("fracturelevel", probe["available_parms"])

        applied = commands.execute(
            "apply-parm-profile",
            {"node": "/obj/geo1/FRACTURE_PROBE", "profile": "rbd-fracture-preview", "values": {"detail_size": 999}, "strict": False},
            hou_module=fake_hou,
        )
        self.assertEqual(
            [(item["parameter"], item["parm"], item["value"]) for item in probe["matched"]],
            [(item["parameter"], item["parm"], item["value"]) for item in applied["matched"]],
        )
        optional_probe = commands.execute(
            "probe-parm-profile",
            {"node": "/obj/geo1/OUT", "profile": "rbd-fracture-preview", "values": {}, "strict": False},
            hou_module=fake_hou,
        )
        self.assertTrue(optional_probe["skipped"])
        self.assertFalse(optional_probe["matched"])

    def test_run_plan_batches_steps_and_honors_error_policy(self):
        fake_hou = FakeHou()
        readonly = commands.execute(
            "run-plan",
            {"steps": [{"command": "context", "payload": {}}]},
            hou_module=fake_hou,
        )
        self.assertTrue(readonly["ok"])
        self.assertEqual(readonly["ran"], 1)
        with self.assertRaises(commands.BridgeCommandError):
            commands.execute(
                "run-plan",
                {"steps": [{"command": "create-node", "payload": {"parent": "/obj/geo1", "type": "null", "name": "NOPE"}}]},
                hou_module=fake_hou,
            )

        state.set_edit_enabled(True)
        stopped = commands.execute(
            "run-plan",
            {
                "steps": [
                    {"command": "create-node", "payload": {"parent": "/obj/geo1", "type": "null", "name": "BATCH_A"}},
                    {"command": "set-parm", "payload": {"node": "/obj/geo1/BATCH_A", "parm": "missing", "value": 1}},
                    {"command": "create-node", "payload": {"parent": "/obj/geo1", "type": "null", "name": "BATCH_B"}},
                ],
                "continue_on_error": False,
            },
            hou_module=fake_hou,
        )
        self.assertFalse(stopped["ok"])
        self.assertEqual(stopped["ran"], 2)
        self.assertTrue(stopped["stopped"])
        self.assertEqual(stopped["failed_step"]["index"], 1)
        self.assertIsNone(fake_hou.node("/obj/geo1/BATCH_B"))

        continued = commands.execute(
            "run-plan",
            {
                "steps": [
                    {"command": "set-parm", "payload": {"node": "/obj/geo1/BATCH_A", "parm": "missing", "value": 1}},
                    {"command": "create-node", "payload": {"parent": "/obj/geo1", "type": "null", "name": "BATCH_C"}},
                ],
                "continue_on_error": True,
            },
            hou_module=fake_hou,
        )
        self.assertFalse(continued["ok"])
        self.assertEqual(continued["ran"], 2)
        self.assertFalse(continued["stopped"])
        self.assertIsNotNone(fake_hou.node("/obj/geo1/BATCH_C"))

    def test_verify_plan_checks_post_run_scene_state(self):
        fake_hou = FakeHou()
        steps = [
            {"command": "create-node", "payload": {"parent": "/obj/geo1", "type": "null", "name": "VERIFY_A"}},
            {"command": "create-node", "payload": {"parent": "/obj/geo1", "type": "null", "name": "VERIFY_B"}},
            {"command": "create-node", "payload": {"parent": "/obj/geo1", "type": "null", "name": "VERIFY_C"}},
            {"command": "create-node", "payload": {"parent": "/obj/geo1", "type": "null", "name": "VERIFY_D"}},
            {"command": "set-parm", "payload": {"node": "/obj/geo1/VERIFY_A", "parm": "tx", "value": 7}},
            {"command": "batch-set-parms", "payload": {"node": "/obj/geo1/VERIFY_B", "values": {"tx": 8}}},
            {"command": "set-comment", "payload": {"node": "/obj/geo1/VERIFY_A", "comment": "Verified direct edit"}},
            {"command": "bypass-node", "payload": {"node": "/obj/geo1/VERIFY_A", "bypass": True}},
            {"command": "set-position", "payload": {"node": "/obj/geo1/VERIFY_A", "x": 3.0, "y": -2.0}},
            {"command": "set-node-color", "payload": {"node": "/obj/geo1/VERIFY_A", "color": [0.1, 0.3, 0.6]}},
            {"command": "connect", "payload": {"src": "/obj/geo1/OUT", "dst": "/obj/geo1/VERIFY_C", "input_index": 0}},
            {"command": "set-input", "payload": {"src": "/obj/geo1/OUT", "dst": "/obj/geo1/VERIFY_A", "input_index": 0}},
            {"command": "disconnect", "payload": {"node": "/obj/geo1/set_pscale1", "input_index": 0}},
            {"command": "set-flags", "payload": {"node": "/obj/geo1/VERIFY_A", "display": True, "render": True}},
            {"command": "layout", "payload": {"path": "/obj/geo1"}},
            {"command": "select", "payload": {"path": "/obj/geo1/VERIFY_A"}},
            {"command": "create-node", "payload": {"parent": "/obj/geo1", "type": "null", "name": "TMP_DELETE"}},
            {"command": "delete-node", "payload": {"node": "/obj/geo1/TMP_DELETE", "confirm": True}},
        ]
        validation = commands.execute("validate-plan", {"steps": steps}, hou_module=fake_hou)
        state.set_edit_enabled(True)
        run_result = commands.execute("run-plan", {"steps": steps}, hou_module=fake_hou)

        verification = commands.execute(
            "verify-plan",
            {"steps": steps, "validation": validation, "run_result": run_result},
            hou_module=fake_hou,
        )

        self.assertTrue(verification["ok"])
        self.assertTrue(verification["verified"])
        self.assertEqual(verification["status"], "pass")
        self.assertEqual(verification["summary"]["failed"], 0)
        self.assertEqual(verification["summary"]["inconclusive"], 0)
        readback = verification["summary"]["direct_edit_readback"]
        self.assertEqual(readback["failed"], 0)
        self.assertEqual(readback["inconclusive"], 0)
        self.assertTrue(readback["proof_ready"])
        self.assertEqual(readback["failed_commands"], [])
        self.assertEqual(readback["inconclusive_commands"], [])
        for command_name in {
            "batch_set_parms",
            "bypass_node",
            "connect",
            "create_node",
            "disconnect",
            "layout",
            "select",
            "set_comment",
            "set_flags",
            "set_input",
            "set_node_color",
            "set_parm",
            "set_position",
        }:
            self.assertIn(command_name, readback["commands"])
        self.assertIsNotNone(fake_hou.node("/obj/geo1/VERIFY_A"))
        self.assertIsNone(fake_hou.node("/obj/geo1/TMP_DELETE"))
        check_kinds = {check["kind"] for check in verification["checks"]}
        self.assertIn("created_path", check_kinds)
        self.assertIn("deleted_path", check_kinds)
        self.assertIn("parm_value", check_kinds)
        self.assertIn("input_connection", check_kinds)
        self.assertIn("comment", check_kinds)
        self.assertIn("bypass", check_kinds)
        self.assertIn("position", check_kinds)
        self.assertIn("node_color", check_kinds)
        self.assertIn("layout_network_readable", check_kinds)
        self.assertIn("selected", check_kinds)
        contract_checks = [
            check
            for check in verification["checks"]
            if check.get("satisfies_direct_edit_contract")
        ]
        self.assertEqual(readback["total"], len(contract_checks))
        self.assertTrue(all(check["direct_edit_contract_read_tools"] for check in contract_checks))
        self.assertTrue(all(check["direct_edit_contract_mcp_read_tools"] for check in contract_checks))
        later_deleted_checks = [
            check
            for check in verification["checks"]
            if check.get("path") == "/obj/geo1/TMP_DELETE" and check["kind"] == "created_path"
        ]
        self.assertTrue(later_deleted_checks)
        self.assertFalse(any(check.get("satisfies_direct_edit_contract") for check in later_deleted_checks))

    def test_verify_plan_fails_when_scene_state_does_not_match(self):
        fake_hou = FakeHou()
        steps = [{"command": "set-parm", "payload": {"node": "/obj/geo1/OUT", "parm": "tx", "value": 9}}]
        validation = commands.execute("validate-plan", {"steps": steps}, hou_module=fake_hou)
        run_result = {
            "ok": True,
            "results": [
                {
                    "index": 0,
                    "response": {
                        "ok": True,
                        "command": "set_parm",
                        "result": {"touched": ["/obj/geo1/OUT"], "parm": "tx"},
                    },
                }
            ],
        }

        verification = commands.execute(
            "verify-plan",
            {"steps": steps, "validation": validation, "run_result": run_result},
            hou_module=fake_hou,
        )

        self.assertFalse(verification["ok"])
        self.assertFalse(verification["verified"])
        self.assertEqual(verification["status"], "failed")
        failed = [check for check in verification["checks"] if check["status"] == "failed"]
        self.assertTrue(any(check["kind"] == "parm_value" and check["actual"] == 1.25 for check in failed))

    def test_verify_plan_fails_when_direct_edit_readback_state_does_not_match(self):
        fake_hou = FakeHou()
        steps = [{"command": "set-comment", "payload": {"node": "/obj/geo1/OUT", "comment": "Expected comment"}}]
        validation = commands.execute("validate-plan", {"steps": steps}, hou_module=fake_hou)
        run_result = {
            "ok": True,
            "results": [
                {
                    "index": 0,
                    "response": {
                        "ok": True,
                        "command": "set_comment",
                        "result": {"touched": ["/obj/geo1/OUT"], "comment": "Expected comment"},
                    },
                }
            ],
        }

        verification = commands.execute(
            "verify-plan",
            {"steps": steps, "validation": validation, "run_result": run_result},
            hou_module=fake_hou,
        )

        self.assertFalse(verification["ok"])
        self.assertFalse(verification["verified"])
        self.assertEqual(verification["status"], "failed")
        failed = [check for check in verification["checks"] if check["status"] == "failed"]
        self.assertTrue(any(check["kind"] == "comment" and check["actual"] == "" for check in failed))
        readback = verification["summary"]["direct_edit_readback"]
        self.assertFalse(readback["proof_ready"])
        self.assertEqual(readback["failed"], 1)
        self.assertEqual(readback["failed_commands"], ["set_comment"])
        self.assertEqual(readback["inconclusive_commands"], [])

    def test_shelf_entry_is_independent_from_blib_agent(self):
        shelf_path = os.path.join(ROOT, "toolbar", "Blib_Houdini_Bridge.shelf")
        with open(shelf_path, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('<toolshelf name="Blib_Houdini_Bridge"', source)
        self.assertIn('<tool name="Blib_hou_bridge"', source)
        self.assertIn('<tool name="Blib_hou_bridge_inspector"', source)
        self.assertIn("shelf.toggle_server()", source)
        self.assertIn("shelf.show_inspector()", source)
        self.assertNotIn("blib_agent", source)

    def test_shelf_reload_keeps_running_server_stoppable(self):
        shelf_path = os.path.join(PYTHON_DIR, "blib_hou_bridge", "shelf.py")
        with open(shelf_path, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("running = server.status().get", source)
        self.assertIn("if not running:", source)
        self.assertIn("importlib.reload(commands)", source)
        self.assertIn("def show_inspector", source)
        self.assertIn('"Reload"', source)
        with open(os.path.join(ROOT, "toolbar", "Blib_Houdini_Bridge.shelf"), encoding="utf-8") as handle:
            shelf_source = handle.read()
        self.assertIn("shelf.set_edit_mode(True)", shelf_source)

    def test_running_shelf_message_handles_status_mode_key(self):
        fake_hou = FakeShelfHou(choice=0)
        result = shelf._running_server_action(
            fake_hou,
            {"host": "127.0.0.1", "port": 12345, "mode": "read", "version": protocol.BRIDGE_VERSION},
        )
        self.assertTrue(result["running"])
        self.assertIn("Mode: read-only", fake_hou.ui.messages[0]["message"])

    def test_cli_maps_edit_command_names(self):
        self.assertEqual(blib_hou._protocol_command("create-node"), "create_node")
        self.assertEqual(blib_hou._protocol_command("edit-mode"), "edit_mode")
        self.assertEqual(blib_hou._protocol_command("set-comment"), "set_comment")
        self.assertEqual(blib_hou._protocol_command("set-flags"), "set_flags")
        self.assertEqual(blib_hou._protocol_command("set-position"), "set_position")
        self.assertEqual(blib_hou._protocol_command("rename-node"), "rename_node")
        self.assertEqual(blib_hou._protocol_command("set-node-color"), "set_node_color")
        self.assertEqual(blib_hou._protocol_command("set-parm-any"), "set_parm_any")
        self.assertEqual(blib_hou._protocol_command("bypass-node"), "bypass_node")
        self.assertEqual(blib_hou._protocol_command("create-network-box"), "create_network_box")
        self.assertEqual(blib_hou._protocol_command("create-sticky-note"), "create_sticky_note")
        self.assertEqual(blib_hou._protocol_command("ensure-parm"), "ensure_parm")
        self.assertEqual(blib_hou._protocol_command("batch-set-parms"), "batch_set_parms")
        self.assertEqual(blib_hou._protocol_command("set-input"), "set_input")
        self.assertEqual(blib_hou._protocol_command("disconnect"), "disconnect")
        self.assertEqual(blib_hou._protocol_command("move-node"), "move_node")
        self.assertEqual(blib_hou._protocol_command("copy-node"), "copy_node")
        self.assertEqual(blib_hou._protocol_command("set-node-shape"), "set_node_shape")
        self.assertEqual(blib_hou._protocol_command("replace-node"), "replace_node")
        self.assertEqual(blib_hou._protocol_command("delete-node"), "delete_node")
        self.assertEqual(blib_hou._parse_value("2"), 2)
        self.assertEqual(blib_hou._parse_value("true"), True)
        self.assertEqual(blib_hou._parse_value("plain"), "plain")
        self.assertEqual(blib_hou._parse_key_values(["detail_size=0.5", "active=true"]), {"detail_size": 0.5, "active": True})
        with self.assertRaises(ValueError):
            blib_hou._parse_key_values(["bad"])

    def test_cli_posts_cleanup_edit_command_payloads(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            session_path = os.path.join(tmpdir, "session.json")
            auth.save_session("127.0.0.1", 12345, "secret", path=session_path, pid=42)
            calls = []
            old_post = blib_hou._post
            try:
                blib_hou._post = lambda host, port, payload, token: calls.append(
                    {"host": host, "port": port, "payload": payload, "token": token}
                ) or {"ok": True, "result": {}}

                self.assertEqual(
                    blib_hou.main(["--session", session_path, "batch-set-parms", "/obj/geo1/OUT", "--value", "tx=3", "--optional"]),
                    0,
                )
                self.assertEqual(
                    blib_hou.main(["--session", session_path, "set-input", "/obj/geo1/OUT", "0", "--clear"]),
                    0,
                )
                self.assertEqual(
                    blib_hou.main(["--session", session_path, "replace-node", "/obj/geo1/OUT", "--type", "null", "--name", "OUT_NEW", "--delete-old", "--confirm"]),
                    0,
                )
                self.assertEqual(
                    blib_hou.main(["--session", session_path, "delete-node", "/obj/geo1/TMP", "--confirm", "--delete-contents"]),
                    0,
                )
            finally:
                blib_hou._post = old_post

        self.assertEqual([call["payload"]["command"] for call in calls], ["batch_set_parms", "set_input", "replace_node", "delete_node"])
        self.assertEqual(calls[0]["payload"]["payload"], {"node": "/obj/geo1/OUT", "values": {"tx": 3}, "required": False})
        self.assertEqual(calls[1]["payload"]["payload"], {"dst": "/obj/geo1/OUT", "input_index": 0, "clear": True})
        self.assertEqual(
            calls[2]["payload"]["payload"],
            {
                "node": "/obj/geo1/OUT",
                "type": "null",
                "name": "OUT_NEW",
                "reconnect_inputs": True,
                "reconnect_outputs": True,
                "delete_old": True,
                "confirm": True,
            },
        )
        self.assertEqual(calls[3]["payload"]["payload"], {"node": "/obj/geo1/TMP", "confirm": True, "delete_contents": True})
        self.assertTrue(all(call["payload"]["token"] == "secret" and call["token"] == "secret" for call in calls))

    def test_cli_run_commands_file_stops_on_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "commands.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    [
                        {"command": "context", "payload": {}},
                        {"command": "run-python", "payload": {}},
                        {"command": "selected", "payload": {}},
                    ],
                    handle,
                )

            calls = []
            old_post = blib_hou._post
            try:
                blib_hou._post = lambda host, port, payload, token: calls.append(payload) or {"ok": True, "result": {}}
                report = blib_hou._run_commands_file({"host": "127.0.0.1", "port": 1, "token": "t"}, path)
            finally:
                blib_hou._post = old_post

            self.assertFalse(report["ok"])
            self.assertEqual(report["ran"], 2)
            self.assertEqual(len(calls), 1)
            self.assertEqual(report["results"][1]["response"]["error"]["code"], "bad_step")

    def test_cli_run_commands_file_can_continue_on_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "commands.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    [
                        {"command": "context", "payload": {}},
                        {"command": "run-python", "payload": {}},
                        {"command": "selected", "payload": {}},
                    ],
                    handle,
                )

            calls = []
            old_post = blib_hou._post
            try:
                blib_hou._post = lambda host, port, payload, token: calls.append(payload) or {"ok": True, "result": {}}
                report = blib_hou._run_commands_file(
                    {"host": "127.0.0.1", "port": 1, "token": "t"},
                    path,
                    continue_on_error=True,
                )
            finally:
                blib_hou._post = old_post

            self.assertFalse(report["ok"])
            self.assertEqual(report["ran"], 3)
            self.assertEqual(len(calls), 2)
            self.assertFalse(report["stopped"])

    def test_cli_workflow_start_creates_evidence_directory_and_empty_plan(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            calls = []
            old_post = blib_hou._post
            try:
                def fake_post(host, port, payload, token):
                    calls.append(payload)
                    return {
                        "ok": True,
                        "result": {
                            "summary": {
                                "network_path": payload["payload"]["path"],
                                "edit_enabled": False,
                                "inferred_purpose": "sop_or_general_node_network",
                                "network_node_count": 3,
                                "key_output_count": 1,
                                "risk_count": 0,
                            },
                            "network": {"path": payload["payload"]["path"]},
                            "semantics": {"network_shape": {"node_count": 3, "wire_count": 2}, "cache_nodes": [], "simulation_nodes": [], "render_nodes": []},
                        },
                    }

                blib_hou._post = fake_post
                report = blib_hou._workflow_start({"host": "127.0.0.1", "port": 1, "token": "t"}, "unit", "/obj/geo1")
            finally:
                blib_hou._post = old_post
                os.chdir(old_cwd)

            workflow_dir = os.path.join(tmpdir, ".blib_hou_workflows", "unit")
            self.assertTrue(report["ok"])
            self.assertEqual(calls[0]["command"], "scene_snapshot")
            self.assertTrue(os.path.exists(os.path.join(workflow_dir, "snapshot_before.json")))
            self.assertEqual(json.loads(Path(workflow_dir, "plan.json").read_text(encoding="utf-8")), [])
            self.assertTrue(os.path.exists(os.path.join(workflow_dir, "summary.md")))
            manifest = json.loads(Path(workflow_dir, "evidence_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["version"], 1)
            self.assertTrue(manifest["evidence"]["has_before_snapshot"])
            self.assertEqual(manifest["semantics"]["before"]["inferred_purpose"], "sop_or_general_node_network")

    def test_cli_workflow_start_template_writes_nonempty_plan_with_explicit_input(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            old_post = blib_hou._post
            try:
                blib_hou._post = lambda host, port, payload, token: {
                    "ok": True,
                    "result": {
                        "summary": {"network_path": payload["payload"]["path"], "edit_enabled": False},
                        "selected": {"count": 0, "nodes": []},
                    },
                }
                report = blib_hou._workflow_start(
                    {"host": "127.0.0.1", "port": 1, "token": "t"},
                    "templated",
                    "/obj/geo1",
                    template="sop-cleanup",
                    template_input="/obj/geo1/OUT",
                    template_options={"name": "quick", "output_name": "OUT_QUICK"},
                )
            finally:
                blib_hou._post = old_post
                os.chdir(old_cwd)

            workflow_dir = Path(tmpdir) / ".blib_hou_workflows" / "templated"
            plan = json.loads((workflow_dir / "plan.json").read_text(encoding="utf-8"))
            provenance = json.loads((workflow_dir / "template_provenance.json").read_text(encoding="utf-8"))
            self.assertTrue(report["ok"])
            self.assertEqual(report["template"]["step_count"], len(plan))
            self.assertTrue(str(report["template"]["provenance_path"]).endswith("template_provenance.json"))
            self.assertEqual(provenance["template"], "sop-cleanup")
            self.assertEqual(provenance["input"], "/obj/geo1/OUT")
            self.assertEqual(provenance["options"]["name"], "quick")
            self.assertEqual(provenance["options"]["output_name"], "OUT_QUICK")
            self.assertEqual(provenance["catalog"]["category"], "cleanup")
            self.assertEqual(provenance["workflow_contract"]["state"], "draft_unreviewed")
            self.assertTrue(provenance["workflow_contract"]["does_not_contact_houdini"])
            self.assertTrue(provenance["workflow_contract"]["does_not_execute"])
            self.assertTrue(provenance["workflow_contract"]["requires_review"])
            self.assertTrue(provenance["workflow_contract"]["requires_validation"])
            self.assertTrue(provenance["workflow_contract"]["requires_bridge_edit_mode_to_run"])
            self.assertEqual(provenance["workflow_contract"]["required_flow"], ["houdini_review_plan", "houdini_validate_plan", "houdini_run_plan", "houdini_verify_plan"])
            self.assertIn("verification", provenance["workflow_contract"]["evidence_expectations"])
            self.assertIn("output_null_exists", provenance["workflow_contract"]["verification_focus"]["success_criteria"])
            self.assertIn("houdini_node_parms", provenance["client_guidance"]["verification_focus"]["read_tools"])
            self.assertIn("houdini_verify_plan", provenance["workflow_contract"]["cannot_report_success_before"])
            self.assertEqual(provenance["client_guidance"]["next_action"], "review_template_plan")
            self.assertFalse(provenance["client_guidance"]["may_execute"])
            self.assertTrue(provenance["client_guidance"]["requires_user_approval_for_writes"])
            self.assertEqual(provenance["plan"]["step_count"], len(plan))
            self.assertEqual(len(provenance["plan"]["sha256"]), 64)
            self.assertGreater(len(plan), 1)
            self.assertIn("OUT_QUICK", json.dumps(plan))
            manifest = json.loads((workflow_dir / "evidence_manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["evidence"]["has_template_provenance"])
            self.assertEqual(manifest["template"]["template"], "sop-cleanup")
            self.assertEqual(manifest["template"]["step_count"], len(plan))
            self.assertEqual(manifest["template"]["options"]["name"], "quick")
            self.assertEqual(manifest["template"]["workflow_contract_state"], "draft_unreviewed")
            self.assertTrue(manifest["template"]["does_not_execute"])
            self.assertEqual(manifest["template"]["required_flow"], ["houdini_review_plan", "houdini_validate_plan", "houdini_run_plan", "houdini_verify_plan"])
            self.assertIn("summary", manifest["template"]["evidence_expectations"])
            self.assertIn("output_flags_set", manifest["template"]["verification_focus"]["success_criteria"])
            self.assertTrue(manifest["template"]["verification_focus_digest"]["ready"])
            self.assertIn("houdini_network", manifest["template"]["verification_focus_digest"]["read_tools"])
            self.assertIn("output_flags_set", manifest["template_verification_focus"]["success_criteria"])
            self.assertEqual(manifest["template_verification_focus"]["template"], "sop-cleanup")
            self.assertIn("houdini_verify_plan", manifest["template"]["cannot_report_success_before"])
            self.assertEqual(manifest["template"]["client_next_action"], "review_template_plan")
            self.assertFalse(manifest["template"]["client_may_execute"])
            checklist = json.loads((workflow_dir / "evidence_checklist.json").read_text(encoding="utf-8"))
            checklist_items = {item["id"]: item for item in checklist["items"]}
            self.assertIn("template_verification_focus", checklist_items)
            self.assertEqual(checklist_items["template_verification_focus"]["status"], "pass")
            proof_report = json.loads((workflow_dir / "proof_report.json").read_text(encoding="utf-8"))
            self.assertTrue(proof_report["template_verification_focus"]["ready"])
            self.assertIn("output_null_exists", proof_report["template_verification_focus"]["success_criteria"])
            self.assertIn("houdini_network", proof_report["client_guidance"]["suggested_tools"])
            self.assertEqual(
                proof_report["client_guidance"]["template_verification_focus"],
                proof_report["template_verification_focus"],
            )
            summary = (workflow_dir / "summary.md").read_text(encoding="utf-8")
            self.assertIn("Template: `sop-cleanup`", summary)
            self.assertIn("contract=draft_unreviewed", summary)
            self.assertIn("executes=no", summary)
            self.assertIn("Template verification focus: ready=yes", summary)
            self.assertIn("output_flags_set", summary)

    def test_cli_workflow_start_template_can_infer_single_selected_input(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            old_post = blib_hou._post
            try:
                blib_hou._post = lambda host, port, payload, token: {
                    "ok": True,
                    "result": {
                        "summary": {"network_path": "/obj/geo1", "edit_enabled": False},
                        "selected": {"count": 1, "nodes": [{"path": "/obj/geo1/OUT"}]},
                    },
                }
                report = blib_hou._workflow_start(
                    {"host": "127.0.0.1", "port": 1, "token": "t"},
                    "infer",
                    "/obj/geo1",
                    template="cache-output",
                    template_options={"cache_path": "$HIP/cache/custom.$F4.bgeo.sc"},
                )
            finally:
                blib_hou._post = old_post
                os.chdir(old_cwd)

            plan = json.loads((Path(tmpdir) / ".blib_hou_workflows" / "infer" / "plan.json").read_text(encoding="utf-8"))
            self.assertTrue(report["ok"])
            self.assertEqual(report["template"]["input"], "/obj/geo1/OUT")
            self.assertIn("$HIP/cache/custom.$F4.bgeo.sc", json.dumps(plan))

    def test_cli_workflow_start_karma_template_records_render_options(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            old_post = blib_hou._post
            try:
                blib_hou._post = lambda host, port, payload, token: {
                    "ok": True,
                    "result": {
                        "summary": {"network_path": "/obj/geo1", "edit_enabled": False},
                        "selected": {"count": 1, "nodes": [{"path": "/obj/geo1/OUT"}]},
                    },
                }
                report = blib_hou._workflow_start(
                    {"host": "127.0.0.1", "port": 1, "token": "t"},
                    "karma",
                    "/obj/geo1",
                    template="karma-solaris-preview",
                    template_options={
                        "name": "shot",
                        "render_path": "$HIP/render/shot/beauty.$F4.exr",
                        "camera_path": "/cameras/render_cam",
                        "resolution": "2048x1152",
                        "samples": 64,
                        "lopnet_name": "SHOT_STAGE",
                    },
                )
            finally:
                blib_hou._post = old_post
                os.chdir(old_cwd)

            workflow_dir = Path(tmpdir) / ".blib_hou_workflows" / "karma"
            plan = json.loads((workflow_dir / "plan.json").read_text(encoding="utf-8"))
            provenance = json.loads((workflow_dir / "template_provenance.json").read_text(encoding="utf-8"))
            self.assertTrue(report["ok"])
            self.assertEqual(provenance["template"], "karma-solaris-preview")
            self.assertEqual(provenance["catalog"]["category"], "render")
            self.assertEqual(provenance["options"]["render_path"], "$HIP/render/shot/beauty.$F4.exr")
            self.assertEqual(provenance["options"]["camera_path"], "/cameras/render_cam")
            self.assertEqual(provenance["options"]["resolution"], "2048x1152")
            self.assertEqual(provenance["options"]["samples"], 64)
            self.assertIn("/obj/SHOT_STAGE/SHOT_USD_RENDER_ROP", json.dumps(plan))

    def test_cli_workflow_start_template_fails_without_unambiguous_input(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            old_post = blib_hou._post
            try:
                blib_hou._post = lambda host, port, payload, token: {
                    "ok": True,
                    "result": {
                        "summary": {"network_path": "/obj/geo1", "edit_enabled": False},
                        "selected": {"count": 2, "nodes": [{"path": "/obj/geo1/A"}, {"path": "/obj/geo1/B"}]},
                    },
                }
                report = blib_hou._workflow_start(
                    {"host": "127.0.0.1", "port": 1, "token": "t"},
                    "ambiguous",
                    "/obj/geo1",
                    template="sop-cleanup",
                )
            finally:
                blib_hou._post = old_post
                os.chdir(old_cwd)

            workflow_dir = Path(tmpdir) / ".blib_hou_workflows" / "ambiguous"
            self.assertFalse(report["ok"])
            self.assertEqual(report["error"]["code"], "template_input_missing")
            self.assertEqual(json.loads((workflow_dir / "plan.json").read_text(encoding="utf-8")), [])

    def test_cli_workflow_review_writes_validation_and_review_and_fails_on_blocker(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflow_dir = Path(tmpdir) / "wf"
            workflow_dir.mkdir()
            (workflow_dir / "plan.json").write_text(
                json.dumps([{"command": "create-node", "payload": {"parent": "/obj/geo1", "type": "null", "name": "OUT_TEST"}}]),
                encoding="utf-8",
            )
            old_post = blib_hou._post
            try:
                def fake_post(host, port, payload, token):
                    if payload["command"] == "validate_plan":
                        return {
                            "ok": True,
                            "result": {
                                "valid": True,
                                "ready_to_run": False,
                                "would_require_edit": True,
                                "blocked_by_edit_mode": True,
                                "step_count": 1,
                            },
                        }
                    if payload["command"] == "review_plan":
                        return {
                            "ok": True,
                            "result": {
                                "blockers": ["Plan contains edit commands while bridge edit mode is off."],
                                "warnings": [],
                                "suggestions": [],
                                "impact": {"created": ["/obj/geo1/OUT_TEST"], "touched": [], "parms": []},
                            },
                        }
                    return {"ok": True, "result": {}}

                blib_hou._post = fake_post
                report = blib_hou._workflow_review({"host": "127.0.0.1", "port": 1, "token": "t"}, str(workflow_dir))
            finally:
                blib_hou._post = old_post

            self.assertFalse(report["ok"])
            self.assertTrue((workflow_dir / "validation.json").exists())
            self.assertTrue((workflow_dir / "review.json").exists())
            self.assertIn("OUT_TEST", (workflow_dir / "summary.md").read_text(encoding="utf-8"))

    def test_cli_workflow_run_requires_explicit_edit_mode_for_edit_plan(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflow_dir = Path(tmpdir) / "wf"
            workflow_dir.mkdir()
            (workflow_dir / "plan.json").write_text(
                json.dumps([{"command": "create-node", "payload": {"parent": "/obj/geo1", "type": "null", "name": "OUT_TEST"}}]),
                encoding="utf-8",
            )
            calls = []
            old_post = blib_hou._post
            try:
                def fake_post(host, port, payload, token):
                    calls.append(payload["command"])
                    if payload["command"] == "validate_plan":
                        return {
                            "ok": True,
                            "result": {
                                "valid": True,
                                "ready_to_run": False,
                                "would_require_edit": True,
                                "blocked_by_edit_mode": True,
                                "step_count": 1,
                            },
                        }
                    if payload["command"] == "review_plan":
                        return {
                            "ok": True,
                            "result": {
                                "blockers": ["Plan contains edit commands while bridge edit mode is off."],
                                "warnings": [],
                                "suggestions": [],
                                "impact": {"created": ["/obj/geo1/OUT_TEST"], "touched": [], "parms": []},
                            },
                        }
                    if payload["command"] == "scene_snapshot":
                        return {"ok": True, "result": {"summary": {"network_path": "/obj", "edit_enabled": False}}}
                    return {"ok": True, "result": {"events": []}}

                blib_hou._post = fake_post
                report = blib_hou._workflow_run(
                    {"host": "127.0.0.1", "port": 1, "token": "t"},
                    str(workflow_dir),
                    enable_edit_mode=False,
                )
            finally:
                blib_hou._post = old_post

            self.assertFalse(report["ok"])
            self.assertEqual(report["error"]["code"], "edit_mode_not_confirmed")
            self.assertNotIn("edit_mode", calls)
            self.assertNotIn("create_node", calls)
            self.assertTrue((workflow_dir / "run_result.json").exists())

    def test_cli_workflow_rollback_drafts_safe_delete_steps_and_unresolved_notes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflow_dir = Path(tmpdir) / "wf"
            workflow_dir.mkdir()
            review = {
                "ok": True,
                "result": {
                    "impact": {
                        "created": ["/obj/geo1/OUT_TEST", "/obj/geo1/TEMP_DELETE"],
                        "touched": ["/obj/geo1/OUT"],
                        "deleted": ["/obj/geo1/TEMP_DELETE", "/obj/geo1/OLD"],
                        "parms": ["/obj/geo1/OUT/tx"],
                    },
                    "rollback_hints": [
                        {"kind": "delete_created_node", "path": "/obj/geo1/OUT_TEST", "command": "create_node", "message": "delete created"},
                        {"kind": "delete_created_node", "path": "/obj/geo1/TEMP_DELETE", "command": "create_node", "message": "delete created"},
                        {"kind": "restore_parameters", "path": "/obj/geo1/OUT", "command": "set_parm", "message": "restore parms"},
                        {"kind": "destructive_delete", "path": "/obj/geo1/OLD", "command": "delete_node", "message": "manual rebuild"},
                    ],
                },
            }
            (workflow_dir / "review.json").write_text(json.dumps(review), encoding="utf-8")

            report = blib_hou._workflow_rollback(str(workflow_dir))

            self.assertTrue(report["ok"])
            self.assertEqual(report["step_count"], 1)
            self.assertEqual(report["unresolved_count"], 3)
            rollback_plan = json.loads((workflow_dir / "rollback_plan.json").read_text(encoding="utf-8"))
            self.assertEqual(rollback_plan["steps"][0]["command"], "delete-node")
            self.assertEqual(rollback_plan["steps"][0]["payload"]["node"], "/obj/geo1/OUT_TEST")
            self.assertEqual(rollback_plan["workflow_contract"]["state"], "draft_unreviewed")
            self.assertTrue(rollback_plan["workflow_contract"]["evidence_only"])
            self.assertTrue(rollback_plan["workflow_contract"]["does_not_execute"])
            self.assertFalse(rollback_plan["workflow_contract"]["auto_execute"])
            self.assertTrue(rollback_plan["workflow_contract"]["requires_user_approval"])
            self.assertFalse(rollback_plan["workflow_contract"]["may_execute"])
            self.assertEqual(
                rollback_plan["workflow_contract"]["required_flow"],
                ["houdini_review_plan", "houdini_validate_plan", "houdini_run_plan", "houdini_verify_plan"],
            )
            self.assertEqual(rollback_plan["client_guidance"]["next_action"], "review_rollback_plan")
            self.assertFalse(rollback_plan["client_guidance"]["may_execute"])
            self.assertNotIn("/obj/geo1/TEMP_DELETE", json.dumps(rollback_plan["steps"]))
            self.assertIn("restore_parameters", [item["kind"] for item in rollback_plan["unresolved"]])
            manifest = json.loads((workflow_dir / "evidence_manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["evidence"]["has_rollback_plan"])
            self.assertEqual(manifest["rollback_plan"]["step_count"], 1)
            self.assertEqual(manifest["rollback_plan"]["unresolved_count"], 3)
            self.assertEqual(manifest["rollback_plan"]["contract_state"], "draft_unreviewed")
            self.assertTrue(manifest["rollback_plan"]["evidence_only"])
            self.assertTrue(manifest["rollback_plan"]["does_not_execute"])
            self.assertFalse(manifest["rollback_plan"]["auto_execute"])
            self.assertTrue(manifest["rollback_plan"]["requires_user_approval"])
            self.assertFalse(manifest["rollback_plan"]["may_execute"])
            self.assertEqual(manifest["rollback_plan"]["client_next_action"], "review_rollback_plan")
            summary = (workflow_dir / "summary.md").read_text(encoding="utf-8")
            self.assertIn("Draft rollback plan: steps=1 unresolved=3", summary)

    def test_cli_workflow_report_auto_drafts_rollback_without_overwriting_existing_plan(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflow_dir = Path(tmpdir) / "wf"
            workflow_dir.mkdir()
            review = {
                "ok": True,
                "result": {
                    "impact": {"created": ["/obj/geo1/OUT_AUTO"], "touched": [], "deleted": [], "parms": []},
                    "rollback_hints": [
                        {"kind": "delete_created_node", "path": "/obj/geo1/OUT_AUTO", "command": "create_node", "message": "delete created"}
                    ],
                },
            }
            (workflow_dir / "review.json").write_text(json.dumps(review), encoding="utf-8")

            report = blib_hou._workflow_report(str(workflow_dir))

            self.assertTrue(report["ok"])
            self.assertTrue(str(report["auto_rollback_plan_path"]).endswith("rollback_plan.json"))
            rollback_plan = json.loads((workflow_dir / "rollback_plan.json").read_text(encoding="utf-8"))
            self.assertEqual(rollback_plan["steps"][0]["payload"]["node"], "/obj/geo1/OUT_AUTO")
            self.assertTrue(rollback_plan["workflow_contract"]["evidence_only"])
            self.assertFalse(rollback_plan["workflow_contract"]["auto_execute"])
            manifest = json.loads((workflow_dir / "evidence_manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["evidence"]["has_rollback_plan"])
            self.assertEqual(manifest["rollback_plan"]["contract_state"], "draft_unreviewed")
            self.assertTrue(manifest["rollback_plan"]["requires_user_approval"])
            checklist = json.loads((workflow_dir / "evidence_checklist.json").read_text(encoding="utf-8"))
            self.assertNotIn("rollback_plan", [item["id"] for item in checklist["items"] if item["status"] == "warn"])

            existing = {"version": 1, "steps": [{"command": "select", "payload": {"path": "/obj/geo1/KEEP"}}], "unresolved": []}
            (workflow_dir / "rollback_plan.json").write_text(json.dumps(existing), encoding="utf-8")
            report = blib_hou._workflow_report(str(workflow_dir))

            self.assertIsNone(report["auto_rollback_plan_path"])
            kept = json.loads((workflow_dir / "rollback_plan.json").read_text(encoding="utf-8"))
            self.assertEqual(kept["steps"][0]["payload"]["path"], "/obj/geo1/KEEP")

    def test_cli_workflow_run_enables_edit_mode_then_runs_and_collects_evidence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflow_dir = Path(tmpdir) / "wf"
            workflow_dir.mkdir()
            (workflow_dir / "plan.json").write_text(
                json.dumps([{"command": "create-node", "payload": {"parent": "/obj/geo1", "type": "null", "name": "OUT_TEST"}}]),
                encoding="utf-8",
            )
            edit_enabled = False
            calls = []
            old_post = blib_hou._post
            try:
                def fake_post(host, port, payload, token):
                    nonlocal edit_enabled
                    calls.append(payload["command"])
                    if payload["command"] == "edit_mode":
                        edit_enabled = bool(payload["payload"].get("enabled"))
                        return {"ok": True, "result": {"edit_enabled": edit_enabled}}
                    if payload["command"] == "validate_plan":
                        return {
                            "ok": True,
                            "result": {
                                "valid": True,
                                "ready_to_run": edit_enabled,
                                "would_require_edit": True,
                                "blocked_by_edit_mode": not edit_enabled,
                                "step_count": 1,
                            },
                        }
                    if payload["command"] == "review_plan":
                        return {
                            "ok": True,
                            "result": {
                                "blockers": [] if edit_enabled else ["Plan contains edit commands while bridge edit mode is off."],
                                "warnings": [],
                                "suggestions": [],
                                "impact": {"created": ["/obj/geo1/OUT_TEST"], "touched": [], "parms": []},
                                "rollback_hints": [
                                    {
                                        "kind": "delete_created_node",
                                        "path": "/obj/geo1/OUT_TEST",
                                        "message": "Rollback can delete the created node if it is still disposable.",
                                    }
                                ],
                                "risk_notes": [
                                    {
                                        "kind": "render",
                                        "severity": "medium",
                                        "path": "/obj/geo1/OUT_TEST",
                                        "message": "Render output should be verified before manual render.",
                                        "verify": "Inspect render path and flags before rendering.",
                                    }
                                ],
                            },
                        }
                    if payload["command"] == "run_plan":
                        return {
                            "ok": True,
                            "result": {
                                "ok": True,
                                "count": len(payload["payload"]["steps"]),
                                "ran": len(payload["payload"]["steps"]),
                                "stopped": False,
                                "results": [{"index": 0, "response": {"ok": True, "command": "create_node", "result": {"created": {"path": "/obj/geo1/OUT_TEST"}}}}],
                            },
                        }
                    if payload["command"] == "verify_plan":
                        self.assertEqual(payload["payload"]["validation"]["result"]["step_count"], 1)
                        self.assertEqual(payload["payload"]["run_result"]["result"]["ran"], 1)
                        return {
                            "ok": True,
                            "result": {
                                "verified": True,
                                "status": "pass",
                                "summary": {
                                    "total": 3,
                                    "passed": 3,
                                    "failed": 0,
                                    "inconclusive": 0,
                                    "direct_edit_readback": {
                                        "total": 1,
                                        "passed": 1,
                                        "failed": 0,
                                        "inconclusive": 0,
                                        "commands": ["create_node"],
                                    },
                                },
                                "checks": [
                                    {
                                        "kind": "created_path",
                                        "status": "pass",
                                        "message": "Created node exists.",
                                        "satisfies_direct_edit_contract": "create_node",
                                        "direct_edit_contract_read_tools": ["node_info", "network"],
                                        "direct_edit_contract_mcp_read_tools": ["houdini_node_info", "houdini_network"],
                                    }
                                ],
                            },
                        }
                    if payload["command"] == "scene_snapshot":
                        scene_after_edit = bool(edit_enabled)
                        return {
                            "ok": True,
                            "result": {
                                "summary": {
                                    "network_path": "/obj/geo1",
                                    "edit_enabled": edit_enabled,
                                    "inferred_purpose": "sop_or_general_node_network",
                                    "network_node_count": 4 if scene_after_edit else 3,
                                    "key_output_count": 1,
                                    "risk_count": 1 if scene_after_edit else 0,
                                },
                                "semantics": {
                                    "network_shape": {"node_count": 4 if scene_after_edit else 3, "wire_count": 3 if scene_after_edit else 2},
                                    "cache_nodes": [],
                                    "simulation_nodes": [],
                                    "render_nodes": [{"path": "/obj/geo1/OUT_TEST", "type": "null"}] if scene_after_edit else [],
                                    "risk_domains": [
                                        {
                                            "domain": "render_settings",
                                            "priority": "medium",
                                            "paths": ["/obj/geo1/OUT_TEST"],
                                            "path_count": 1,
                                            "suggested_tools": ["houdini_node_info", "houdini_node_parms"],
                                            "workflow_templates": ["karma-solaris-preview"],
                                        }
                                    ] if scene_after_edit else [],
                                    "workflow_suggestions": [
                                        {"template": "karma-solaris-preview", "risk_domains": ["render_settings"]}
                                    ] if scene_after_edit else [],
                                    "scene_understanding": {
                                        "state": "risk_domain_detected" if scene_after_edit else "network_context",
                                        "primary_risk_domain": "render_settings" if scene_after_edit else "none",
                                        "primary_focus_path": "/obj/geo1/OUT_TEST" if scene_after_edit else "",
                                        "first_read_tools": ["houdini_node_info", "houdini_node_parms"] if scene_after_edit else ["houdini_scene_snapshot"],
                                        "read_targets": [{"path": "/obj/geo1/OUT_TEST", "reason": "render output"}] if scene_after_edit else [],
                                        "suggested_templates": ["karma-solaris-preview"] if scene_after_edit else [],
                                        "required_write_flow": ["houdini_review_plan", "houdini_validate_plan", "houdini_run_plan", "houdini_verify_plan"],
                                        "may_execute": False,
                                        "safe_to_run_direct_edits": False,
                                    },
                                },
                            },
                        }
                    if payload["command"] == "rpc_log":
                        return {"ok": True, "result": {"events": [{"command": "create_node", "ok": True, "status": 200}]}}
                    return {"ok": True, "result": {}}

                blib_hou._post = fake_post
                report = blib_hou._workflow_run(
                    {"host": "127.0.0.1", "port": 1, "token": "t"},
                    str(workflow_dir),
                    enable_edit_mode=True,
                )
            finally:
                blib_hou._post = old_post

            self.assertTrue(report["ok"])
            self.assertTrue(report["verified"])
            self.assertEqual(report["verification_status"], "pass")
            self.assertEqual(
                calls,
                ["scene_snapshot", "validate_plan", "review_plan", "edit_mode", "validate_plan", "review_plan", "run_plan", "verify_plan", "rpc_log", "scene_snapshot"],
            )
            self.assertTrue((workflow_dir / "run_result.json").exists())
            self.assertTrue((workflow_dir / "verification.json").exists())
            self.assertTrue((workflow_dir / "rpc_log.json").exists())
            self.assertTrue((workflow_dir / "snapshot_after.json").exists())
            self.assertTrue((workflow_dir / "proof_report.json").exists())
            summary = (workflow_dir / "summary.md").read_text(encoding="utf-8")
            self.assertIn("## Verification", summary)
            self.assertIn("Status: `pass`", summary)
            self.assertIn("## Rollback Hints", summary)
            self.assertIn("delete_created_node", summary)
            self.assertIn("/obj/geo1/OUT_TEST", summary)
            manifest = json.loads((workflow_dir / "evidence_manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["evidence"]["has_verification"])
            self.assertTrue(manifest["evidence"]["has_after_snapshot"])
            self.assertTrue(manifest["evidence"]["has_evidence_checklist"])
            self.assertTrue(manifest["evidence"]["has_proof_report"])
            self.assertEqual(manifest["status"]["verification_status"], "pass")
            self.assertEqual(manifest["proof_report"]["verdict"], "proven")
            self.assertTrue(manifest["proof_report"]["proof_ready"])
            self.assertEqual(manifest["semantics"]["after"]["wire_count"], 3)
            self.assertTrue(manifest["scene_evidence"]["exists"])
            self.assertEqual(manifest["scene_evidence"]["before"]["scene_understanding"]["primary_risk_domain"], "none")
            self.assertEqual(manifest["scene_evidence"]["after"]["scene_understanding"]["primary_risk_domain"], "render_settings")
            self.assertEqual(manifest["scene_evidence"]["after"]["scene_understanding"]["primary_focus_path"], "/obj/geo1/OUT_TEST")
            self.assertIn("render_settings", manifest["scene_evidence"]["transition"]["risk_domains_added"])
            self.assertEqual(manifest["scene_evidence"]["transition"]["node_count_delta"], 1)
            self.assertEqual(manifest["scene_evidence"]["transition"]["wire_count_delta"], 1)
            self.assertFalse(manifest["scene_evidence"]["may_execute"])
            self.assertFalse(manifest["scene_evidence"]["safe_to_run_direct_edits"])
            self.assertEqual(manifest["rollback_hints"][0]["kind"], "delete_created_node")
            self.assertEqual(manifest["risk_notes"][0]["kind"], "render")
            self.assertTrue(manifest["artifact_integrity"]["all_existing_hashed"])
            self.assertGreaterEqual(manifest["artifact_integrity"]["existing_count"], 8)
            self.assertEqual(manifest["artifact_integrity"]["hashed_count"], manifest["artifact_integrity"]["existing_count"])
            self.assertIn("template_provenance", manifest["artifact_integrity"]["missing_artifacts"])
            artifact_index = {item["key"]: item for item in manifest["artifacts"]}
            self.assertEqual(len(artifact_index["plan"]["sha256"]), 64)
            self.assertEqual(len(artifact_index["summary"]["sha256"]), 64)
            self.assertEqual(len(artifact_index["verification"]["sha256"]), 64)
            self.assertEqual(artifact_index["snapshot_after"]["bytes"], (workflow_dir / "snapshot_after.json").stat().st_size)
            proof_report = json.loads((workflow_dir / "proof_report.json").read_text(encoding="utf-8"))
            self.assertEqual(proof_report["verdict"], "proven")
            self.assertTrue(proof_report["proof_ready"])
            self.assertTrue(proof_report["direct_edit_readback"]["proof_ready"])
            self.assertEqual(proof_report["direct_edit_readback"]["commands"], ["create_node"])
            self.assertEqual(proof_report["next_action"], "report_success")
            self.assertFalse(proof_report["rollback_recommended"])
            self.assertIn("client_guidance", proof_report)
            self.assertEqual(proof_report["client_guidance"]["next_action"], "report_success")
            self.assertIn("houdini://workflow/wf/proof-report", proof_report["client_guidance"]["mcp_resources"])
            self.assertIn("houdini://workflow/wf/summary", proof_report["client_guidance"]["mcp_resources"])
            self.assertIn("houdini_scene_snapshot", proof_report["client_guidance"]["suggested_tools"])
            checklist = json.loads((workflow_dir / "evidence_checklist.json").read_text(encoding="utf-8"))
            self.assertTrue(checklist["proof_ready"])
            self.assertEqual(checklist["status"], "pass")
            self.assertEqual(checklist["summary"]["required_passed"], checklist["summary"]["required_total"])
            rollback_plan = json.loads((workflow_dir / "rollback_plan.json").read_text(encoding="utf-8"))
            self.assertEqual(rollback_plan["steps"][0]["payload"]["node"], "/obj/geo1/OUT_TEST")
            self.assertEqual(rollback_plan["workflow_contract"]["state"], "draft_unreviewed")
            self.assertFalse(rollback_plan["workflow_contract"]["may_execute"])
            self.assertTrue(manifest["evidence"]["has_rollback_plan"])
            self.assertEqual(manifest["rollback_plan"]["step_count"], 1)
            self.assertTrue(manifest["rollback_plan"]["evidence_only"])
            self.assertIn("## Evidence Checklist", summary)
            self.assertIn("## Scene Evidence", summary)
            self.assertIn("primary_changed=yes", summary)
            self.assertIn("render_settings", summary)
            self.assertIn("may_execute=no", summary)
            self.assertIn("Evidence readiness: pass proof_ready=yes", summary)
            self.assertIn("Proof verdict: proven proof_ready=yes", summary)
            self.assertIn("Direct edit readback: proof_ready=yes total=1 passed=1 failed=0 inconclusive=0 commands=`create_node`", summary)
            self.assertIn("Risk notes: render `/obj/geo1/OUT_TEST`", summary)

    def test_cli_workflow_run_minimal_evidence_skips_after_snapshot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflow_dir = Path(tmpdir) / "wf"
            workflow_dir.mkdir()
            (workflow_dir / "plan.json").write_text(json.dumps([{"command": "context", "payload": {}}]), encoding="utf-8")
            calls = []
            old_post = blib_hou._post
            try:
                def fake_post(host, port, payload, token):
                    calls.append(payload["command"])
                    if payload["command"] == "validate_plan":
                        return {"ok": True, "result": {"valid": True, "ready_to_run": True, "would_require_edit": False, "blocked_by_edit_mode": False, "step_count": 1}}
                    if payload["command"] == "review_plan":
                        return {"ok": True, "result": {"blockers": [], "warnings": [], "suggestions": [], "impact": {"created": [], "touched": [], "parms": []}}}
                    if payload["command"] == "run_plan":
                        return {"ok": True, "result": {"ok": True, "count": 1, "ran": 1, "stopped": False, "results": []}}
                    if payload["command"] == "verify_plan":
                        return {"ok": True, "result": {"verified": False, "status": "inconclusive", "summary": {"total": 0, "passed": 0, "failed": 0, "inconclusive": 0}, "checks": []}}
                    if payload["command"] == "rpc_log":
                        return {"ok": True, "result": {"events": []}}
                    return {"ok": True, "result": {}}

                blib_hou._post = fake_post
                report = blib_hou._workflow_run(
                    {"host": "127.0.0.1", "port": 1, "token": "t"},
                    str(workflow_dir),
                    enable_edit_mode=False,
                    evidence="minimal",
                )
            finally:
                blib_hou._post = old_post

            self.assertTrue(report["ok"])
            self.assertIn("run_plan", calls)
            self.assertIn("verify_plan", calls)
            self.assertIn("rpc_log", calls)
            self.assertEqual(calls.count("scene_snapshot"), 1)
            self.assertTrue((workflow_dir / "run_result.json").exists())
            self.assertTrue((workflow_dir / "verification.json").exists())
            self.assertFalse((workflow_dir / "snapshot_after.json").exists())

    def test_cli_workflow_run_full_evidence_writes_visual_capture_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflow_dir = Path(tmpdir) / "wf"
            workflow_dir.mkdir()
            (workflow_dir / "plan.json").write_text(json.dumps([{"command": "context", "payload": {}}]), encoding="utf-8")
            old_post = blib_hou._post
            try:
                def fake_post(host, port, payload, token):
                    if payload["command"] == "validate_plan":
                        return {"ok": True, "result": {"valid": True, "ready_to_run": True, "would_require_edit": False, "blocked_by_edit_mode": False, "step_count": 1}}
                    if payload["command"] == "review_plan":
                        return {"ok": True, "result": {"blockers": [], "warnings": [], "suggestions": [], "impact": {"created": [], "touched": [], "deleted": [], "parms": []}}}
                    if payload["command"] == "run_plan":
                        return {"ok": True, "result": {"ok": True, "count": 1, "ran": 1, "stopped": False, "results": []}}
                    if payload["command"] == "verify_plan":
                        return {"ok": True, "result": {"verified": True, "status": "pass", "summary": {"total": 1, "passed": 1, "failed": 0, "inconclusive": 0}, "checks": []}}
                    if payload["command"] == "rpc_log":
                        return {"ok": True, "result": {"events": []}}
                    if payload["command"] == "scene_snapshot":
                        include_viewport = bool(payload["payload"].get("include_viewport"))
                        return {
                            "ok": True,
                            "result": {
                                "summary": {"network_path": "/obj", "edit_enabled": False},
                                "semantics": {"network_shape": {"node_count": 1, "wire_count": 0}, "cache_nodes": [], "simulation_nodes": [], "render_nodes": []},
                                "viewport": {
                                    "included": include_viewport,
                                    "ok": include_viewport,
                                    "path": "C:/Temp/blib_hou_bridge/screenshots/full_001.png" if include_viewport else "",
                                    "width": 1280 if include_viewport else None,
                                    "height": 720 if include_viewport else None,
                                    "bytes": 42 if include_viewport else None,
                                    "viewport": "persp1" if include_viewport else "",
                                },
                            },
                        }
                    return {"ok": True, "result": {}}

                blib_hou._post = fake_post
                report = blib_hou._workflow_run(
                    {"host": "127.0.0.1", "port": 1, "token": "t"},
                    str(workflow_dir),
                    evidence="full",
                )
            finally:
                blib_hou._post = old_post

            self.assertTrue(report["ok"])
            visual = json.loads((workflow_dir / "visual_evidence.json").read_text(encoding="utf-8"))
            self.assertEqual(visual["status"], "captured")
            self.assertTrue(visual["captured"])
            self.assertEqual(visual["path"], "C:/Temp/blib_hou_bridge/screenshots/full_001.png")
            self.assertEqual(visual["proof_role"], "supporting_capture_only")
            self.assertEqual(visual["semantic_verdict"], "not_judged")
            self.assertTrue(visual["requires_visual_judgment"])
            self.assertFalse(visual["may_report_visual_success"])
            self.assertFalse(visual["visual_success_claim_allowed"])
            manifest = json.loads((workflow_dir / "evidence_manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["evidence"]["has_visual_evidence"])
            self.assertTrue(manifest["evidence"]["has_screenshot"])
            self.assertEqual(manifest["visual"]["status"], "captured")
            self.assertEqual(manifest["visual"]["viewport"], "persp1")
            self.assertEqual(manifest["visual"]["proof_role"], "supporting_capture_only")
            self.assertEqual(manifest["visual"]["semantic_verdict"], "not_judged")
            self.assertFalse(manifest["visual"]["may_report_visual_success"])
            checklist = json.loads((workflow_dir / "evidence_checklist.json").read_text(encoding="utf-8"))
            self.assertTrue(checklist["proof_ready"])
            self.assertEqual(checklist["status"], "pass")
            self.assertEqual(checklist["summary"]["failure_count"], 0)
            proof_report = json.loads((workflow_dir / "proof_report.json").read_text(encoding="utf-8"))
            self.assertEqual(proof_report["visual"]["semantic_verdict"], "not_judged")
            self.assertFalse(proof_report["visual"]["may_report_visual_success"])
            self.assertEqual(proof_report["client_guidance"]["visual_guidance"]["semantic_verdict"], "not_judged")
            self.assertFalse(proof_report["client_guidance"]["visual_guidance"]["may_report_visual_success"])
            self.assertIn("visual-evidence", proof_report["client_guidance"]["read_order"])
            summary = (workflow_dir / "summary.md").read_text(encoding="utf-8")
            self.assertIn("Visual evidence: captured 1280x720", summary)
            self.assertIn("semantic=not_judged", summary)
            self.assertIn("visual_success=no", summary)
            self.assertIn("full_001.png", summary)

    def test_cli_workflow_run_fails_when_verification_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflow_dir = Path(tmpdir) / "wf"
            workflow_dir.mkdir()
            (workflow_dir / "plan.json").write_text(
                json.dumps([{"command": "create-node", "payload": {"parent": "/obj/geo1", "type": "null", "name": "OUT_FAILED"}}]),
                encoding="utf-8",
            )
            edit_enabled = False
            old_post = blib_hou._post
            try:
                def fake_post(host, port, payload, token):
                    nonlocal edit_enabled
                    if payload["command"] == "edit_mode":
                        edit_enabled = True
                        return {"ok": True, "result": {"edit_enabled": True}}
                    if payload["command"] == "validate_plan":
                        return {"ok": True, "result": {"valid": True, "ready_to_run": edit_enabled, "would_require_edit": True, "blocked_by_edit_mode": not edit_enabled, "step_count": 1}}
                    if payload["command"] == "review_plan":
                        return {
                            "ok": True,
                            "result": {
                                "blockers": [] if edit_enabled else ["Plan contains edit commands while bridge edit mode is off."],
                                "warnings": [],
                                "suggestions": [],
                                "impact": {"created": ["/obj/geo1/OUT_FAILED"], "touched": [], "deleted": [], "parms": []},
                                "rollback_hints": [
                                    {
                                        "kind": "delete_created_node",
                                        "path": "/obj/geo1/OUT_FAILED",
                                        "command": "create_node",
                                        "message": "Rollback can delete the failed created node.",
                                    }
                                ],
                            },
                        }
                    if payload["command"] == "run_plan":
                        return {"ok": True, "result": {"ok": True, "count": 1, "ran": 1, "stopped": False, "results": [{"index": 0, "response": {"ok": True, "command": "create_node", "result": {"created": {"path": "/obj/geo1/OUT_FAILED"}}}}]}}
                    if payload["command"] == "verify_plan":
                        return {
                            "ok": False,
                            "result": {
                                "verified": False,
                                "status": "failed",
                                "summary": {
                                    "total": 2,
                                    "passed": 1,
                                    "failed": 1,
                                    "inconclusive": 0,
                                    "direct_edit_readback": {
                                        "total": 1,
                                        "passed": 0,
                                        "failed": 1,
                                        "inconclusive": 0,
                                        "commands": ["create_node"],
                                        "failed_commands": ["create_node"],
                                        "inconclusive_commands": [],
                                        "proof_ready": False,
                                    },
                                },
                                "checks": [
                                    {
                                        "kind": "created_path",
                                        "status": "failed",
                                        "message": "Created node was not found.",
                                        "step_index": 0,
                                        "satisfies_direct_edit_contract": "create_node",
                                    }
                                ],
                            },
                        }
                    if payload["command"] == "rpc_log":
                        return {"ok": True, "result": {"events": []}}
                    return {"ok": True, "result": {}}

                blib_hou._post = fake_post
                report = blib_hou._workflow_run(
                    {"host": "127.0.0.1", "port": 1, "token": "t"},
                    str(workflow_dir),
                    enable_edit_mode=True,
                    evidence="minimal",
                )
            finally:
                blib_hou._post = old_post

            self.assertFalse(report["ok"])
            self.assertFalse(report["verified"])
            self.assertEqual(report["verification_status"], "failed")
            summary = (workflow_dir / "summary.md").read_text(encoding="utf-8")
            self.assertIn("Status: `failed`", summary)
            self.assertIn("created_path", summary)
            self.assertIn("Direct edit readback: proof_ready=no total=1 passed=0 failed=1 inconclusive=0 commands=`create_node` failed_commands=`create_node`", summary)
            proof_report = json.loads((workflow_dir / "proof_report.json").read_text(encoding="utf-8"))
            self.assertEqual(proof_report["verdict"], "failed")
            self.assertFalse(proof_report["direct_edit_readback"]["proof_ready"])
            self.assertEqual(proof_report["direct_edit_readback"]["failed_commands"], ["create_node"])
            self.assertTrue(proof_report["rollback_recommended"])
            self.assertIn("houdini://workflow/wf/rollback-plan", proof_report["client_guidance"]["mcp_resources"])
            rollback_guidance = proof_report["client_guidance"]["rollback_guidance"]
            self.assertTrue(rollback_guidance["recommended"])
            self.assertFalse(rollback_guidance["auto_execute"])
            self.assertEqual(rollback_guidance["resource"], "houdini://workflow/wf/rollback-plan")
            self.assertEqual(
                rollback_guidance["required_review_flow"],
                ["houdini_review_plan", "houdini_validate_plan", "houdini_run_plan", "houdini_verify_plan"],
            )
            repair_guidance = proof_report["client_guidance"]["repair_guidance"]
            self.assertTrue(repair_guidance["recommended"])
            self.assertEqual(repair_guidance["action"], "draft_repair_plan")
            self.assertFalse(repair_guidance["auto_execute"])
            self.assertFalse(repair_guidance["may_execute"])
            self.assertTrue(repair_guidance["requires_user_approval"])
            self.assertIn("houdini_scene_snapshot", repair_guidance["diagnostic_read_tools"])
            self.assertIn("created_path", repair_guidance["failed_check_kinds"])
            self.assertEqual(repair_guidance["direct_edit_failed_commands"], ["create_node"])
            self.assertEqual(repair_guidance["direct_edit_readback"]["failed_commands"], ["create_node"])
            self.assertFalse(repair_guidance["direct_edit_readback"]["proof_ready"])
            self.assertEqual(
                repair_guidance["required_review_flow"],
                ["houdini_review_plan", "houdini_validate_plan", "houdini_run_plan", "houdini_verify_plan"],
            )
            manifest = json.loads((workflow_dir / "evidence_manifest.json").read_text(encoding="utf-8"))
            manifest_rollback_guidance = manifest["proof_report"]["rollback_guidance"]
            self.assertTrue(manifest_rollback_guidance["recommended"])
            self.assertFalse(manifest_rollback_guidance["auto_execute"])
            self.assertEqual(manifest_rollback_guidance["resource"], "houdini://workflow/wf/rollback-plan")
            self.assertEqual(manifest_rollback_guidance["direct_edit_readback"]["failed_commands"], ["create_node"])
            self.assertEqual(
                manifest_rollback_guidance["required_review_flow"],
                ["houdini_review_plan", "houdini_validate_plan", "houdini_run_plan", "houdini_verify_plan"],
            )
            manifest_repair_guidance = manifest["proof_report"]["repair_guidance"]
            self.assertTrue(manifest_repair_guidance["recommended"])
            self.assertFalse(manifest_repair_guidance["auto_execute"])
            self.assertFalse(manifest_repair_guidance["may_execute"])
            self.assertIn("houdini_node_parms", manifest_repair_guidance["diagnostic_read_tools"])
            self.assertEqual(manifest_repair_guidance["direct_edit_failed_commands"], ["create_node"])
            rollback_plan = json.loads((workflow_dir / "rollback_plan.json").read_text(encoding="utf-8"))
            self.assertEqual(rollback_plan["steps"][0]["payload"]["node"], "/obj/geo1/OUT_FAILED")
            self.assertFalse(rollback_plan["workflow_contract"]["auto_execute"])
            self.assertTrue(rollback_plan["workflow_contract"]["requires_review"])

    def test_cli_workflow_run_writes_profile_and_timing_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflow_dir = Path(tmpdir) / "wf"
            workflow_dir.mkdir()
            plan = [
                {"command": "apply-parm-profile", "payload": {"node": "/obj/geo1/FRACTURE", "profile": "rbd-fracture-preview", "values": {"detail_size": 999}}},
                {"command": "layout", "payload": {"path": "/obj/geo1"}},
            ]
            (workflow_dir / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
            edit_enabled = False
            old_post = blib_hou._post
            try:
                def fake_post(host, port, payload, token):
                    nonlocal edit_enabled
                    if payload["command"] == "edit_mode":
                        edit_enabled = True
                        return {"ok": True, "result": {"edit_enabled": True}}
                    if payload["command"] == "validate_plan":
                        return {"ok": True, "result": {"valid": True, "ready_to_run": edit_enabled, "would_require_edit": True, "blocked_by_edit_mode": not edit_enabled, "step_count": 2}}
                    if payload["command"] == "review_plan":
                        return {"ok": True, "result": {"blockers": [] if edit_enabled else ["Plan contains edit commands while bridge edit mode is off."], "warnings": [], "suggestions": [], "impact": {"created": [], "touched": ["/obj/geo1/FRACTURE"], "parms": ["/obj/geo1/FRACTURE/<rbd-fracture-preview>"]}}}
                    if payload["command"] == "run_plan":
                        return {
                            "ok": True,
                            "result": {
                                "ok": True,
                                "count": 2,
                                "ran": 2,
                                "stopped": False,
                                "results": [
                                    {
                                        "index": 0,
                                        "response": {
                                            "ok": True,
                                            "command": "apply_parm_profile",
                                            "duration_ms": 12.5,
                                            "result": {
                                                "profile": "rbd-fracture-preview",
                                                "touched": ["/obj/geo1/FRACTURE"],
                                                "applied": [{"parameter": "detail_size", "parm": "fracturelevel", "value": 10.0}],
                                                "matched": [{"parameter": "detail_size", "parm": "fracturelevel", "value": 10.0, "clamped": True}],
                                                "skipped": [{"parameter": "material_type", "candidates": ["materialtype"]}],
                                                "clamped": [{"parameter": "detail_size", "parm": "fracturelevel", "input": 999, "value": 10.0}],
                                                "unresolved": [],
                                            },
                                        },
                                    },
                                    {"index": 1, "response": {"ok": True, "command": "layout", "duration_ms": 3.0, "result": {}}},
                                ],
                            },
                        }
                    if payload["command"] == "rpc_log":
                        return {"ok": True, "result": {"events": []}}
                    return {"ok": True, "result": {}}

                blib_hou._post = fake_post
                report = blib_hou._workflow_run(
                    {"host": "127.0.0.1", "port": 1, "token": "t"},
                    str(workflow_dir),
                    enable_edit_mode=True,
                    evidence="minimal",
                )
            finally:
                blib_hou._post = old_post

            self.assertTrue(report["ok"])
            profile_report = json.loads((workflow_dir / "profile_report.json").read_text(encoding="utf-8"))
            self.assertEqual(profile_report["profile_step_count"], 1)
            self.assertEqual(profile_report["applied_count"], 1)
            self.assertEqual(profile_report["skipped_count"], 1)
            self.assertEqual(profile_report["clamped_count"], 1)
            self.assertEqual(profile_report["slow_steps"][0]["command"], "apply_parm_profile")
            summary = (workflow_dir / "summary.md").read_text(encoding="utf-8")
            self.assertIn("## Runtime", summary)
            self.assertIn("## Profile Calibration", summary)
            self.assertIn("rbd-fracture-preview/detail_size", summary)

    def test_cli_workflow_report_handles_missing_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflow_dir = Path(tmpdir) / "wf"
            report = blib_hou._workflow_report(str(workflow_dir))
            summary = (workflow_dir / "summary.md").read_text(encoding="utf-8")
            self.assertTrue(report["ok"])
            self.assertTrue((workflow_dir / "evidence_checklist.json").exists())
            checklist = json.loads((workflow_dir / "evidence_checklist.json").read_text(encoding="utf-8"))
            self.assertEqual(checklist["status"], "fail")
            self.assertFalse(checklist["proof_ready"])
            proof_report = json.loads((workflow_dir / "proof_report.json").read_text(encoding="utf-8"))
            self.assertEqual(proof_report["verdict"], "incomplete")
            self.assertFalse(proof_report["proof_ready"])
            self.assertEqual(proof_report["next_action"], "collect_missing_evidence")
            self.assertIn("houdini://workflow/wf/evidence-checklist", proof_report["client_guidance"]["mcp_resources"])
            self.assertIn("houdini_verify_plan", proof_report["client_guidance"]["suggested_tools"])
            self.assertIn("before_snapshot", proof_report["client_guidance"]["blocked_by"])
            self.assertIn("Network path: `unknown`", summary)
            self.assertIn("Evidence readiness: fail proof_ready=no", summary)
            self.assertIn("Proof verdict: incomplete proof_ready=no", summary)
            self.assertIn("## Profile Calibration", summary)
            self.assertIn("No RPC events recorded.", summary)

    def test_cli_proof_report_rejects_failed_direct_edit_readback_even_when_verification_passes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflow_dir = Path(tmpdir) / "wf"
            workflow_dir.mkdir()
            artifacts = {
                "plan": [{"command": "set-comment", "payload": {"node": "/obj/geo1/OUT", "comment": "Expected"}}],
                "snapshot_before": {"ok": True, "result": {"summary": {"network_path": "/obj/geo1"}}},
                "validation": {"ok": True, "result": {"valid": True}},
                "review": {"ok": True, "result": {"impact": {"created": [], "touched": ["/obj/geo1/OUT"], "deleted": [], "parms": []}}},
                "run_result": {"ok": True, "result": {"ok": True, "ran": 1}},
                "verification": {
                    "ok": True,
                    "result": {
                        "verified": True,
                        "status": "pass",
                        "summary": {
                            "total": 1,
                            "passed": 1,
                            "failed": 0,
                            "inconclusive": 0,
                            "direct_edit_readback": {
                                "total": 1,
                                "passed": 0,
                                "failed": 1,
                                "inconclusive": 0,
                                "commands": ["set_comment"],
                                "failed_commands": ["set_comment"],
                                "inconclusive_commands": [],
                                "proof_ready": False,
                            },
                        },
                        "checks": [],
                    },
                },
                "rpc_log": {"ok": True, "result": {"events": []}},
            }

            checklist = blib_hou._workflow_evidence_checklist(workflow_dir, artifacts)
            checklist_items = {item["id"]: item for item in checklist["items"]}
            self.assertEqual(checklist["status"], "fail")
            self.assertFalse(checklist["proof_ready"])
            self.assertEqual(checklist_items["direct_edit_readback"]["status"], "fail")
            self.assertEqual(checklist_items["direct_edit_readback"]["level"], "required")
            self.assertIn("must be proof-ready", checklist_items["direct_edit_readback"]["message"])
            artifacts["evidence_checklist"] = checklist

            proof_report = blib_hou._workflow_proof_report(workflow_dir, artifacts)

            self.assertEqual(proof_report["verdict"], "failed")
            self.assertFalse(proof_report["proof_ready"])
            self.assertEqual(proof_report["next_action"], "review_failed_checks")
            self.assertEqual(proof_report["summary"]["direct_edit_readback_failed"], 1)
            self.assertEqual(proof_report["direct_edit_readback"]["failed_commands"], ["set_comment"])
            reasons = {item["kind"]: item for item in proof_report["reasons"]}
            self.assertIn("direct_edit_readback_failed", reasons)
            self.assertEqual(reasons["direct_edit_readback_failed"]["commands"], ["set_comment"])
            self.assertTrue(proof_report["client_guidance"]["repair_guidance"]["recommended"])
            self.assertFalse(proof_report["client_guidance"]["repair_guidance"]["may_execute"])
            self.assertEqual(proof_report["client_guidance"]["repair_guidance"]["direct_edit_failed_commands"], ["set_comment"])
            self.assertEqual(
                proof_report["client_guidance"]["repair_guidance"]["direct_edit_readback"]["failed_commands"],
                ["set_comment"],
            )

    def test_edit_mode_command_toggles_gate_without_scene_edit(self):
        self.assertFalse(state.edit_enabled())
        result = commands.execute("edit-mode", {"enabled": True}, hou_module=FakeHou())
        self.assertTrue(result["edit_enabled"])
        result = commands.execute("edit-mode", {"enabled": False}, hou_module=FakeHou())
        self.assertFalse(result["edit_enabled"])

    def test_bridge_button_exposes_edit_mode_menu(self):
        shelf_path = os.path.join(PYTHON_DIR, "blib_hou_bridge", "shelf.py")
        with open(shelf_path, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('"Edit On"', source)
        self.assertIn('"Edit Off"', source)
        self.assertIn("state.set_edit_enabled(True)", source)

    def test_write_houdini_package_generates_receiver_local_root(self):
        tool = _load_tool_module("write_houdini_package")
        with tempfile.TemporaryDirectory() as tmpdir:
            release_root = Path(tmpdir) / "bridge"
            (release_root / "toolbar").mkdir(parents=True)
            (release_root / "toolbar" / "Blib_Houdini_Bridge.shelf").write_text("<shelfDocument />", encoding="utf-8")
            (release_root / "scripts" / "python" / "blib_hou_bridge").mkdir(parents=True)
            (release_root / "scripts" / "python" / "blib_hou_bridge" / "__init__.py").write_text("", encoding="utf-8")
            (release_root / "scripts" / "python" / "blib_hou_mcp").mkdir(parents=True)
            (release_root / "scripts" / "python" / "blib_hou_mcp" / "__init__.py").write_text("", encoding="utf-8")
            output = Path(tmpdir) / "packages" / "Blib_Houdini_Bridge.json"

            tool.write_package(output, release_root)

            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["path"], "$BLIB_HOUDINI_BRIDGE")
            self.assertEqual(payload["env"][0]["BLIB_HOUDINI_BRIDGE"], release_root.resolve().as_posix())

    def test_clean_release_artifacts_removes_runtime_files(self):
        tool = _load_tool_module("clean_release_artifacts")
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cache_dir = root / "pkg" / "__pycache__"
            cache_dir.mkdir(parents=True)
            pyc = cache_dir / "module.cpython-310.pyc"
            pyc.write_bytes(b"cache")
            workflow_dir = root / ".blib_hou_workflows" / "acceptance_smoke"
            workflow_dir.mkdir(parents=True)
            (workflow_dir / "proof_report.json").write_text("{}", encoding="utf-8")
            keep = root / "pkg" / "module.py"
            keep.write_text("print('keep')\n", encoding="utf-8")

            dry_run = tool.clean_artifacts(root, dry_run=True)
            self.assertIn(cache_dir, dry_run)
            self.assertIn(root / ".blib_hou_workflows", dry_run)
            self.assertTrue(pyc.exists())

            removed = tool.clean_artifacts(root)
            self.assertIn(cache_dir, removed)
            self.assertIn(root / ".blib_hou_workflows", removed)
            self.assertFalse(cache_dir.exists())
            self.assertFalse((root / ".blib_hou_workflows").exists())
            self.assertTrue(keep.exists())

    def test_acceptance_smoke_reports_offline_next_action(self):
        tool = _load_tool_module("acceptance_smoke")
        offline = {
            "returncode": 2,
            "stderr": "",
            "json": {"ok": False, "error": {"code": "offline", "message": "No session"}},
        }
        message = tool._diagnostic_message("doctor", offline)
        self.assertIn("Start Houdini", message)

    def test_acceptance_smoke_write_plan_uses_reviewable_workflow_commands(self):
        tool = _load_tool_module("acceptance_smoke")
        with tempfile.TemporaryDirectory() as tmpdir:
            workflow_dir = Path(tmpdir) / "wf"
            tool._write_acceptance_plan(workflow_dir, "/obj", "BLIB_ACCEPT")
            plan = json.loads((workflow_dir / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual([step["command"] for step in plan], ["create-node", "set-comment"])
            self.assertEqual(plan[1]["payload"]["node"], "/obj/BLIB_ACCEPT")

    def test_release_validator_checks_standalone_install_markers(self):
        tool = _load_tool_module("validate_bridge_release")
        self.assertIn("tools/acceptance_smoke.py", tool.REQUIRED_PATHS)
        self.assertIn("tools/clean_release_artifacts.py", tool.REQUIRED_PATHS)
        self.assertIn("tools/write_houdini_package.py", tool.REQUIRED_PATHS)
        self.assertIn("tools\\acceptance_smoke.py", tool.REQUIRED_DOC_MARKERS["README.md"])
        self.assertIn("tools\\clean_release_artifacts.py", tool.REQUIRED_DOC_MARKERS["README.md"])
        self.assertIn("tools\\write_houdini_package.py", tool.REQUIRED_DOC_MARKERS["README.md"])
        self.assertIn("python tools\\validate_bridge_release.py --strict", tool.REQUIRED_DOC_MARKERS["README.md"])
        self.assertIn("python tools\\validate_bridge_release.py --public --strict", tool.REQUIRED_DOC_MARKERS["README.md"])
        self.assertIn("blib_agent", tool.FORBIDDEN_SHELF_IMPORTS)

    def test_release_validator_strict_mode_blocks_warnings(self):
        tool = _load_tool_module("validate_bridge_release")
        old_validate = tool.validate_release
        try:
            tool.validate_release = lambda public=False: (0, 1, [("WARN", "example warning")])
            self.assertEqual(tool.main(["--strict"]), 1)
            self.assertEqual(tool.main([]), 0)
        finally:
            tool.validate_release = old_validate

    def test_release_validator_public_mode_checks_open_source_surface(self):
        tool = _load_tool_module("validate_bridge_release")
        error_count, warn_count, findings = tool.validate_release(public=False)
        self.assertFalse(any("license" in message.lower() for _, message in findings))
        public_error_count, public_warn_count, public_findings = tool.validate_release(public=True)
        self.assertGreaterEqual(public_warn_count, warn_count)
        self.assertIn("LICENSE", tool.REQUIRED_PATHS)
        self.assertIn("CONTRIBUTING.md", tool.REQUIRED_PATHS)
        self.assertIn("D:/houdini_plugins", tool.PUBLIC_FORBIDDEN_TEXT)


def _post_rpc(port, payload, header_token=None, raw_body=None):
    data = raw_body if raw_body is not None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if header_token is not None:
        headers["X-Blib-Bridge-Token"] = header_token
    request = urllib.request.Request(
        "http://127.0.0.1:%s/rpc" % port,
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
