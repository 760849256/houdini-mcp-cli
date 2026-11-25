"""Pure target resolution shared by client and host workflows."""
from __future__ import annotations

class TargetError(ValueError):
    def __init__(self, message, candidates):
        super().__init__(message)
        self.candidates = candidates


def _selected_path(ctx):
    selection = ctx.get("selection", [])
    if len(selection) != 1:
        raise TargetError("Select one node or provide an explicit absolute path.", selection)
    return selection[0]["path"]


def _resolve_path(value, ctx):
    if not isinstance(value, str):
        raise ValueError("Node paths must be strings.")
    root, slash, suffix = value.partition("/")
    if root == "@selected":
        base = _selected_path(ctx)
    elif root == "@selected_parent":
        base = _selected_path(ctx).rsplit("/", 1)[0] or "/"
    elif root == "@network":
        base = ctx.get("current_network")
        if not base:
            raise TargetError("No current network. Supply an explicit path.", ctx.get("selection", []))
    elif value.startswith("/"):
        return value
    else:
        raise ValueError("Use an absolute path or @selected, @selected_parent, @network.")
    return base.rstrip("/") + ("/" + suffix if slash else "") or "/"


def _resolve_edit_step(step, ctx):
    command, payload = step["command"], step["payload"]
    if command == "create_node":
        if "parent" not in payload:
            if len(ctx.get("selection", [])) > 1:
                raise TargetError("Multiple selections: supply the creation parent explicitly.", ctx["selection"])
            payload["parent"] = "@selected_parent" if ctx.get("selection") else "@network"
    elif command == "layout":
        payload.setdefault("path", "@network")
    elif command == "select":
        payload.setdefault("path", "@selected")
    elif command in {"connect", "set_input"}:
        if command == "connect":
            payload.setdefault("src", "@selected")
        # Destination is deliberately never guessed.
    elif command not in {"set_frame"}:
        payload.setdefault("node", "@selected")
    for key in {"node", "parent", "path", "src", "dst"} & payload.keys():
        payload[key] = _resolve_path(payload[key], ctx)
