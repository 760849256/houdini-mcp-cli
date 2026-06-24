"""Codex MCP registration helpers for the Houdini shelf entrypoint."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


SERVER_NAME = "blib-houdini-bridge"
CONFIG_HEADER = "[mcp_servers.%s]" % SERVER_NAME


def ensure_codex_mcp_registered(
    config_path: str | os.PathLike | None = None,
    python_executable: str | None = None,
    script_path: str | os.PathLike | None = None,
) -> dict:
    """Register the local MCP adapter in Codex config.toml if needed."""
    config = Path(config_path).expanduser() if config_path is not None else _default_codex_config_path()
    command = _normalize_command(python_executable or _default_python_executable())
    script = str(Path(script_path).resolve()) if script_path is not None else _default_mcp_script_path()
    block = _codex_config_block(command, script)

    existing = config.read_text(encoding="utf-8") if config.exists() else ""
    current = _extract_server_block(existing)
    if current == block:
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
        candidates.append(Path(hfs) / "bin" / "hython.exe")
        candidates.extend(sorted(Path(hfs).glob("python*/python.exe"), reverse=True))

    current = Path(sys.executable)
    if _looks_like_python_executable(current):
        candidates.append(current)
    for name in ("python.exe", "python3.exe", "hython.exe"):
        candidates.append(current.parent / name)
    return candidates


def _looks_like_python_executable(path: Path) -> bool:
    name = path.name.lower()
    return name in {"python.exe", "python3.exe", "hython.exe", "python", "python3", "hython"} or name.startswith("python")


def _codex_config_block(command: str, script: str) -> str:
    return "\n".join(
        [
            CONFIG_HEADER,
            "command = %s" % _toml_string(command),
            "args = [",
            "  %s," % _toml_string(script),
            "]",
        ]
    )


def _toml_string(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


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
