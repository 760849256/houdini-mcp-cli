"""Local MCP client configuration and Codex registration."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path


SERVER_NAME = "blib-houdini-bridge"
CONFIG_HEADER = "[mcp_servers.%s]" % SERVER_NAME
MCP_CLIENTS = {"codex": "Codex", "antigravity": "Antigravity", "generic": "通用 MCP"}


def client_launch_args() -> list[str]:
    script = _default_mcp_script_path()
    if Path(script).is_file():
        return [script]
    return ["-m", "blib_hou_mcp"]


def instance_server_name(instance: str | None, hip_name: str = "", pid: int | None = None) -> str:
    if not instance:
        return SERVER_NAME
    identity = hashlib.sha256(instance.encode("utf-8")).hexdigest()[:8]
    stem = Path(hip_name).stem.casefold() if hip_name else ""
    label = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")[:30].rstrip("-")
    suffix = f"{pid}-{identity}" if type(pid) is int else identity
    return f"blib-houdini-{label}-{suffix}" if label else f"{SERVER_NAME}-{suffix}"


def validate_server_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name):
        raise ValueError("MCP name must start with an ASCII letter and contain up to 64 letters, digits, hyphens or underscores.")
    return name


def render_client_config(client: str, command: str, args: list[str], server_name: str = SERVER_NAME) -> str:
    server_name = validate_server_name(server_name)
    if client == "codex":
        lines = [f"[mcp_servers.{server_name}]", "command = %s" % json.dumps(command, ensure_ascii=False), "args = ["]
        lines.extend("  %s," % json.dumps(arg, ensure_ascii=False) for arg in args)
        return "\n".join([*lines, "]"])
    if client in ("antigravity", "generic"):
        return json.dumps({"mcpServers": {server_name: {"command": command, "args": args}}}, indent=2)
    raise ValueError("Unknown MCP client: %s" % client)


def client_command(instance: str | None = None) -> tuple[str, list[str]]:
    program = _normalize_command(_default_python_executable())
    arguments = client_launch_args()
    if instance:
        arguments.extend(["--instance", instance])
    return program, arguments


def client_config_preview(client: str, instance: str | None = None, server_name: str | None = None) -> str:
    name = instance_server_name(instance) if server_name is None else server_name
    return render_client_config(client, *client_command(instance), server_name=name)


def ensure_codex_mcp_registered(
    config_path: str | os.PathLike | None = None,
    python_executable: str | None = None,
    script_path: str | os.PathLike | None = None,
) -> dict:
    """Register the local MCP adapter in Codex config.toml if needed."""
    config = Path(config_path).expanduser() if config_path is not None else _default_codex_config_path()
    command = _normalize_command(python_executable or _default_python_executable())
    script = str(Path(script_path).resolve()) if script_path is not None else _default_mcp_script_path()
    args = [script] if script_path is not None else client_launch_args()
    block = render_client_config("codex", command, args)

    existing = config.read_text(encoding="utf-8") if config.exists() else ""
    current = _extract_server_block(existing)
    if current == block or _configured_entrypoint_matches(current, args):
        return {
            "ok": True,
            "changed": False,
            "config_path": str(config),
            "command": command,
            "script": script,
            "message": "Codex MCP config already contains %s." % SERVER_NAME,
        }

    config.parent.mkdir(parents=True, exist_ok=True)
    backup_path = ""
    if config.exists():
        backup = _next_backup_path(config)
        shutil.copy2(config, backup)
        backup_path = str(backup)

    updated = _replace_or_append_server_block(existing, block)
    config.write_text(updated, encoding="utf-8")
    return {
        "ok": True,
        "changed": True,
        "config_path": str(config),
        "backup_path": backup_path,
        "command": command,
        "script": script,
        "message": "Registered %s in Codex config. Restart Codex or open a new session." % SERVER_NAME,
    }


def codex_registration_status(config_path: str | os.PathLike | None = None) -> dict:
    config = Path(config_path).expanduser() if config_path is not None else _default_codex_config_path()
    text = config.read_text(encoding="utf-8") if config.exists() else ""
    block = _extract_server_block(text)
    return {
        "registered": bool(block),
        "config_path": str(config),
        "block": block,
    }


def _default_codex_config_path() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / "config.toml"
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    return home / ".codex" / "config.toml"


def _default_mcp_script_path() -> str:
    root = Path(__file__).resolve().parents[3]
    return str(root / "scripts" / "cli" / "blib_hou_mcp.py")


def _default_python_executable() -> str:
    for candidate in _python_executable_candidates():
        if candidate and candidate.exists() and candidate.is_file():
            return str(candidate)
    for command in ("python", "python3", "py"):
        resolved = shutil.which(command)
        if resolved:
            return resolved
    if _looks_like_python_executable(Path(sys.executable)):
        return sys.executable
    return "python"


def _normalize_command(value: str) -> str:
    text = str(value)
    if "\\" in text or "/" in text or ":" in text or Path(text).exists():
        return str(Path(text).resolve())
    return text


def _python_executable_candidates() -> list[Path]:
    candidates: list[Path] = []
    override = os.environ.get("BLIB_CODEX_PYTHON")
    if override:
        candidates.append(Path(override))

    home = Path(os.environ.get("USERPROFILE") or Path.home())
    codex_root = home / ".cache" / "codex-runtimes"
    candidates.append(codex_root / "codex-primary-runtime" / "dependencies" / "python" / "python.exe")
    if codex_root.exists():
        candidates.extend(sorted(codex_root.glob("*/dependencies/python/python.exe"), reverse=True))

    hfs = os.environ.get("HFS")
    if hfs:
        candidates.extend(sorted(Path(hfs).glob("python*/python.exe"), reverse=True))
        candidates.append(Path(hfs) / "bin" / "hython.exe")

    current = Path(sys.executable)
    if _looks_like_python_executable(current):
        candidates.append(current)
    for name in ("python.exe", "python3.exe", "hython.exe"):
        candidates.append(current.parent / name)
    return candidates


def _looks_like_python_executable(path: Path) -> bool:
    name = path.name.lower()
    return name in {"python.exe", "python3.exe", "hython.exe", "python", "python3", "hython"} or name.startswith("python")


def _configured_entrypoint_matches(block: str, args: list[str]) -> bool:
    """Keep custom instance, session and profile options for this entry point."""
    match = re.search(r"(?ms)^args\s*=\s*(\[.*?\])", block)
    if not match:
        return False
    try:
        arguments = ast.literal_eval(match.group(1))
        if not isinstance(arguments, list) or not arguments or not isinstance(arguments[0], str):
            return False
        if args[0] == "-m":
            return arguments[:2] == args
        return os.path.normcase(os.path.abspath(arguments[0])) == os.path.normcase(os.path.abspath(args[0]))
    except (ValueError, SyntaxError):
        return False


def _extract_server_block(text: str) -> str:
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() == CONFIG_HEADER:
            start = index
            break
    if start is None:
        return ""

    end = len(lines)
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            end = index
            break
    return "\n".join(lines[start:end]).strip()


def _replace_or_append_server_block(text: str, block: str) -> str:
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() == CONFIG_HEADER:
            start = index
            break
    if start is None:
        prefix = text.rstrip()
        return (prefix + "\n\n" if prefix else "") + block + "\n"

    end = len(lines)
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            end = index
            break
    updated_lines = lines[:start] + block.splitlines() + lines[end:]
    return "\n".join(updated_lines).rstrip() + "\n"


def _next_backup_path(config: Path) -> Path:
    candidate = config.with_suffix(config.suffix + ".blib-bridge.bak")
    if not candidate.exists():
        return candidate
    for index in range(1, 1000):
        candidate = config.with_suffix(config.suffix + ".blib-bridge.%03d.bak" % index)
        if not candidate.exists():
            return candidate
    raise RuntimeError("Could not choose a Codex config backup path.")
