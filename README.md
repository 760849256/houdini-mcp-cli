# Blib Houdini Bridge

Inspect, preview, and edit a running Houdini scene from a local MCP client or the command line.

## Quick start

For a standalone installation, run from the repository root.

```powershell
python -m pip install -e .
python tools/install_houdini_package.py
```

1. Restart Houdini and open **Blib Bridge → Bridge**. In the full Blib Tools package, open **Blib AI → Bridge**.
2. Open **更多 → AI 接入** to check Codex registration or copy your client's configuration. Reload the MCP client after setup.
3. Run `houdini_context` to confirm the target HIP. Use **更多 → 测试连接** if connection setup fails.

## Ports and multiple Houdini windows

Edit the number after `127.0.0.1:` and press Enter or click **应用端口**. Give each Houdini window a different port. Click the copy icon beside the port and paste the connection prompt into the corresponding AI conversation. All windows can share one MCP configuration. [Instance matching](docs/HOUDINI_MCP.md#multiple-houdini-instances)

Bridge starts in editable mode, including custom Python. Enable **只读** for inspection and previews.

[MCP setup and tools](docs/HOUDINI_MCP.md) · [Connection recovery](docs/CONNECTION_RECOVERY.md) · [Compatibility](docs/COMPATIBILITY.md) · [Installation and release](docs/BRIDGE_ONLY_RELEASE.md) · [Security](SECURITY.md) · [License](LICENSE)

<details>
<summary>中文快速开始</summary>

独立安装使用上面的两条命令。重启 Houdini 后打开 **Blib Bridge → Bridge**，完整工具集从 **Blib AI → Bridge** 打开。直接修改端口数字并按回车生效。端口旁的复制图标可把当前窗口的连接信息发给 AI。 [MCP 使用说明](docs/HOUDINI_MCP.md)

</details>
