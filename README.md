# Blib Houdini Bridge

Inspect, preview, and edit a running Houdini scene from Codex, other local MCP clients, or the command line.

## Quick start

For a standalone installation, run from the repository root:

```powershell
python -m pip install -e .
python tools/install_houdini_package.py
```

1. Launch Houdini and click **Blib Bridge → Bridge**. With the full Blib Tools package, open **Blib AI → Bridge**.
2. Open **AI 接入** to check Codex registration or copy configuration for a stdio MCP client. Reload the MCP client after its first registration.
3. Run `houdini_context` to confirm the target HIP. Use **测试连接** in the Bridge panel to diagnose connection issues.

Bridge starts in editable mode, including custom Python. Enable **只读** to restrict clients to inspection and previews.

## Multiple Houdini windows

Click **复制给 AI** in the intended Houdini window to copy a prompt containing its editable control code. Paste it into the corresponding AI conversation. All windows can share one MCP configuration. [Instance matching](docs/HOUDINI_MCP.md#multiple-houdini-instances)

[MCP setup and tools](docs/HOUDINI_MCP.md) · [Connection recovery](docs/CONNECTION_RECOVERY.md) · [Compatibility](docs/COMPATIBILITY.md) · [Installation and release](docs/BRIDGE_ONLY_RELEASE.md) · [Security](SECURITY.md) · [License](LICENSE)

<details>
<summary>中文快速开始</summary>

独立安装使用上面的两条命令，重启 Houdini 后点击 **Blib Bridge → Bridge**。完整工具集从 **Blib AI → Bridge** 打开。在 **AI 接入** 检查或复制 MCP 配置，多窗口用 **复制给 AI** 发给各自的 AI 对话。 [MCP 使用说明](docs/HOUDINI_MCP.md)

</details>
