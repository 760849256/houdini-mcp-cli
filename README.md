# Blib Houdini Bridge

Safe local Houdini control for external tools and AI clients, with CLI and MCP
support.

This folder is the bridge-only release. It is independent from the larger Blib
Tools production toolkit.

Start here:

- [Release guide](docs/BRIDGE_ONLY_RELEASE.md)
- [MCP guide](docs/HOUDINI_MCP.md)
- [Compatibility](docs/COMPATIBILITY.md)
- [Security policy](SECURITY.md)
- [Contributing guide](CONTRIBUTING.md)
- [License](LICENSE)

## Install and Verify

From this `bridge/` directory, install the CLI entry points:

```powershell
python -m pip install -e .
```

Create a Houdini package JSON for this machine. The checked-in
`Blib_Houdini_Bridge.json` is a template with a placeholder path; generate a
local package file before installing:

```powershell
python tools\write_houdini_package.py --output Blib_Houdini_Bridge.local.json
```

Copy the generated package file into a Houdini packages directory and rename it
to `Blib_Houdini_Bridge.json` if needed.

Start Houdini, open the `Blib Bridge` shelf, and click `Bridge`.

After starting the shelf server, verify the connection:

```powershell
python scripts\cli\blib_hou.py doctor
python scripts\cli\blib_hou.py scene-snapshot --path /obj
python scripts\cli\blib_hou_mcp.py --status
```

For controlled write acceptance and proof-based handoff checks, see the
[release guide](docs/BRIDGE_ONLY_RELEASE.md).

Before publishing or handing the folder to another user, run:

```powershell
python -m unittest tests.test_blib_hou_bridge tests.test_blib_hou_mcp
python tools\clean_release_artifacts.py
python tools\validate_bridge_release.py
python tools\validate_bridge_release.py --strict
python tools\validate_bridge_release.py --public --strict
```

`tools\clean_release_artifacts.py` removes local workflow evidence from the
release tree after you have reviewed the acceptance result. The default
validation gate is for internal handoff to another Houdini user; add `--public`
before publishing outside the team.

## 中文说明

Blib Houdini Bridge 是一个独立的 Houdini 本地控制桥接工具，面向外部工具、
AI 客户端、CLI 和 MCP 客户端。这个仓库只包含 bridge，不包含完整的 Blib
Tools 生产工具集。

入口文档：

- [发布说明](docs/BRIDGE_ONLY_RELEASE.md)
- [MCP 说明](docs/HOUDINI_MCP.md)
- [兼容性](docs/COMPATIBILITY.md)
- [安全策略](SECURITY.md)
- [贡献指南](CONTRIBUTING.md)
- [许可证](LICENSE)

### 安装与验证

在 `bridge/` 目录中安装 CLI 入口：

```powershell
python -m pip install -e .
```

为当前机器生成 Houdini package JSON。仓库中的
`Blib_Houdini_Bridge.json` 是带占位路径的模板，安装前请先生成本机可用的
package 文件：

```powershell
python tools\write_houdini_package.py --output Blib_Houdini_Bridge.local.json
```

把生成的 package 文件复制到 Houdini 的 packages 目录中；如有需要，将文件名
改为 `Blib_Houdini_Bridge.json`。

启动 Houdini，打开 `Blib Bridge` shelf，然后点击 `Bridge` 启动本地服务。

服务启动后，运行以下命令确认连接和 MCP 适配器状态：

```powershell
python scripts\cli\blib_hou.py doctor
python scripts\cli\blib_hou.py scene-snapshot --path /obj
python scripts\cli\blib_hou_mcp.py --status
```

如果需要带可控写入和证据记录的验收流程，请查看
[发布说明](docs/BRIDGE_ONLY_RELEASE.md)。

发布或交付给其他用户前，运行：

```powershell
python -m unittest tests.test_blib_hou_bridge tests.test_blib_hou_mcp
python tools\clean_release_artifacts.py
python tools\validate_bridge_release.py
python tools\validate_bridge_release.py --strict
python tools\validate_bridge_release.py --public --strict
```

`tools\clean_release_artifacts.py` 会在你检查完验收结果后，清理发布目录中的本地
运行痕迹。默认验证门槛适用于团队内部交付；公开发布前请额外通过
`--public --strict` 检查。
