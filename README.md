# Blib Houdini Bridge

Blib Houdini Bridge lets Codex, MCP clients, CLI scripts, and similar local
tools connect to Houdini and control a running scene in a safer, reviewable
way.

It runs a small local bridge inside Houdini, then exposes that bridge through:

- a Houdini shelf tool
- a command-line client
- an MCP adapter for Codex-style tool use

This repository contains only the standalone bridge. It does not include the
full Blib Tools production toolkit.

## What You Can Do With It

- Let Codex or another MCP-capable assistant read the current Houdini scene.
- Run simple CLI commands such as status checks and scene snapshots.
- Keep Houdini-side edits gated behind explicit edit mode.
- Review, validate, run, and verify controlled workflows instead of blindly
  executing changes.
- Use the bridge as a local control layer for your own Houdini automation tools.

The bridge is local-first: it binds to `127.0.0.1`, uses a per-session token,
and keeps read operations separate from write operations.

## Install

From this `bridge/` directory, install the CLI entry points:

```powershell
python -m pip install -e .
```

Generate a Houdini package file for your machine:

```powershell
python tools\write_houdini_package.py --output Blib_Houdini_Bridge.local.json
```

Copy the generated package file into a Houdini packages directory. Rename it to
`Blib_Houdini_Bridge.json` if that is how you prefer to load Houdini packages.

The checked-in `Blib_Houdini_Bridge.json` is only a template. Generate a local
one before installing so the bridge path points at your own checkout.

## Start Houdini

1. Start Houdini.
2. Open the `Blib Bridge` shelf.
3. Click `Bridge` to start the local server.
4. Keep edit mode off until you intentionally want a tool to write to the
   scene.

After the shelf server is running, check the connection:

```powershell
python scripts\cli\blib_hou.py doctor
python scripts\cli\blib_hou.py scene-snapshot --path /obj
python scripts\cli\blib_hou_mcp.py --status
```

If those commands work, the bridge is ready for CLI use and MCP clients.

## Use With Codex Or Similar Tools

The MCP adapter is the intended path for Codex-style clients:

```powershell
python scripts\cli\blib_hou_mcp.py --print-config
python scripts\cli\blib_hou_mcp.py --status
```

Use the printed config in your MCP client. Once connected, the client can ask
the bridge for scene context, inspect nodes, and use the bridge workflow tools.

For details about the MCP surface, see [docs/HOUDINI_MCP.md](docs/HOUDINI_MCP.md).

## Safety Model

The bridge is designed to make external control visible and reversible:

- read commands work without enabling edit mode
- write commands require Houdini-side edit mode
- workflow commands support review, validation, execution, and verification
- session tokens are local and should not be pasted into logs or prompts

For release checks, controlled handoff, and deeper validation notes, see
[docs/BRIDGE_ONLY_RELEASE.md](docs/BRIDGE_ONLY_RELEASE.md).

## More Docs

- [MCP guide](docs/HOUDINI_MCP.md)
- [Release guide](docs/BRIDGE_ONLY_RELEASE.md)
- [Compatibility](docs/COMPATIBILITY.md)
- [Security policy](SECURITY.md)
- [Contributing guide](CONTRIBUTING.md)
- [License](LICENSE)

## 中文说明

Blib Houdini Bridge 可以让 Codex、MCP 客户端、CLI 脚本，或者类似的本地工具
连接到正在运行的 Houdini，并以更可控、可检查的方式读取和操作场景。

它会在 Houdini 里启动一个本地 bridge，然后通过三种方式对外使用：

- Houdini shelf 工具
- 命令行客户端
- 面向 Codex 这类工具的 MCP 适配器

这个仓库只包含独立的 bridge，不包含完整的 Blib Tools 生产工具集。

### 它可以做什么

- 让 Codex 或其他支持 MCP 的助手读取当前 Houdini 场景。
- 用 CLI 命令检查 bridge 状态、读取 scene snapshot。
- 把写入操作限制在 Houdini 明确开启 edit mode 之后。
- 对复杂操作先 review、validate，再执行和验证结果。
- 作为你自己的 Houdini 自动化工具的本地控制层。

bridge 默认只监听 `127.0.0.1`，每次会话都有 token，并且把读取和写入操作分开。

### 安装

在 `bridge/` 目录中安装 CLI 入口：

```powershell
python -m pip install -e .
```

为当前机器生成 Houdini package 文件：

```powershell
python tools\write_houdini_package.py --output Blib_Houdini_Bridge.local.json
```

把生成的 package 文件复制到 Houdini 的 packages 目录中。如果你的加载方式需要
固定文件名，可以把它改名为 `Blib_Houdini_Bridge.json`。

仓库里的 `Blib_Houdini_Bridge.json` 只是模板。正式安装前，请先生成指向你本机
路径的 package 文件。

### 启动 Houdini

1. 启动 Houdini。
2. 打开 `Blib Bridge` shelf。
3. 点击 `Bridge`，启动本地服务。
4. 在确实需要外部工具写入场景之前，保持 edit mode 关闭。

服务启动后，可以用下面的命令确认连接状态：

```powershell
python scripts\cli\blib_hou.py doctor
python scripts\cli\blib_hou.py scene-snapshot --path /obj
python scripts\cli\blib_hou_mcp.py --status
```

这些命令能正常返回，就说明 CLI 和 MCP 客户端已经可以使用这个 bridge。

### 配合 Codex 或类似工具使用

Codex 这类工具建议通过 MCP 适配器连接：

```powershell
python scripts\cli\blib_hou_mcp.py --print-config
python scripts\cli\blib_hou_mcp.py --status
```

把打印出来的配置填到你的 MCP 客户端里。连接后，客户端就可以读取场景上下文、
检查节点，并通过 bridge 的工作流工具执行受控操作。

MCP 接口细节见 [docs/HOUDINI_MCP.md](docs/HOUDINI_MCP.md)。

### 安全设计

这个 bridge 的目标不是让外部工具随便改场景，而是让控制过程尽量可见、可审查：

- 读取命令不需要开启 edit mode
- 写入命令需要在 Houdini 侧明确开启 edit mode
- 工作流命令支持 review、validate、run、verify
- session token 只用于本地连接，不要贴到日志或提示词里

发布检查、交付流程和更完整的验证说明见
[docs/BRIDGE_ONLY_RELEASE.md](docs/BRIDGE_ONLY_RELEASE.md)。
