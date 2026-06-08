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

## Fast Path: Connect Codex To Houdini

If your goal is simply "let Codex read and control Houdini", do this:

1. Install this repo:

```powershell
python -m pip install -e .
```

2. Generate the Houdini package file and load it in Houdini:

```powershell
python tools\write_houdini_package.py --output Blib_Houdini_Bridge.local.json
```

Copy `Blib_Houdini_Bridge.local.json` into a Houdini packages directory. Rename
it to `Blib_Houdini_Bridge.json` if needed.

3. Start Houdini, open the `Blib Bridge` shelf, and click `Bridge`.

4. Print the Codex MCP config:

```powershell
python scripts\cli\blib_hou_mcp.py --print-codex-config
```

Paste the printed TOML into:

```text
%UserProfile%\.codex\config.toml
```

Restart Codex or open a new Codex session. Then ask Codex:

```text
Use the blib-houdini-bridge MCP server to read houdini://adapter/status,
then take a read-only scene snapshot of /obj.
```

That is the shortest path. The sections below explain what each step is doing.

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

The MCP adapter is what lets Codex talk to Houdini. The connection has three
pieces:

1. Houdini runs the `Blib Bridge` shelf server.
2. Codex starts `scripts\cli\blib_hou_mcp.py` as a local MCP server.
3. That MCP server reads Houdini's current bridge session and exposes Houdini
   tools to Codex.

Start with Houdini already open and the shelf server running. Then check the MCP
adapter from this repo:

```powershell
python scripts\cli\blib_hou_mcp.py --status
```

If `readiness.status` is `ready` or `degraded`, the MCP adapter can see the
bridge.

### Add It To Codex

Codex uses TOML config. This repo can print the Codex-ready block for you:

```powershell
python scripts\cli\blib_hou_mcp.py --print-codex-config
```

Paste the output into your Codex config file:

```text
%UserProfile%\.codex\config.toml
```

It will look like this:

```toml
[mcp_servers.blib-houdini-bridge]
command = "C:\\Path\\To\\python.exe"
args = [
  "C:\\Path\\To\\houdini-mcp-cli\\scripts\\cli\\blib_hou_mcp.py",
]
```

Restart Codex, or start a new Codex session. If you use Codex CLI, you can also
check whether Codex has loaded the server:

```powershell
codex mcp list
```

In Codex, ask it to check the Houdini MCP connection:

```text
Use the blib-houdini-bridge MCP server to read houdini://adapter/status,
then take a read-only scene snapshot of /obj.
```

If the connection is working, Codex should see tools and resources with names
such as:

- `houdini://adapter/status`
- `houdini://scene/current`
- `houdini_scene_snapshot`
- `houdini_node_info`
- `houdini_edit_mode`

For other MCP clients that expect JSON, use:

```powershell
python scripts\cli\blib_hou_mcp.py --print-config
```

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

### 最快路径：让 Codex 连上 Houdini

如果你的目标只是“让 Codex 能读取和控制 Houdini”，先按这 4 步走：

1. 安装这个仓库：

```powershell
python -m pip install -e .
```

2. 生成 Houdini package 文件，并让 Houdini 加载它：

```powershell
python tools\write_houdini_package.py --output Blib_Houdini_Bridge.local.json
```

把 `Blib_Houdini_Bridge.local.json` 复制到 Houdini 的 packages 目录中。如果你的
加载方式需要固定文件名，可以把它改名为 `Blib_Houdini_Bridge.json`。

3. 启动 Houdini，打开 `Blib Bridge` shelf，点击 `Bridge`。

4. 打印 Codex 可直接使用的 MCP 配置：

```powershell
python scripts\cli\blib_hou_mcp.py --print-codex-config
```

把输出的 TOML 粘贴到：

```text
%UserProfile%\.codex\config.toml
```

重启 Codex，或者打开一个新的 Codex 会话。然后在 Codex 里问：

```text
Use the blib-houdini-bridge MCP server to read houdini://adapter/status,
then take a read-only scene snapshot of /obj.
```

这就是最短路径。后面的内容只是解释每一步在做什么。

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

MCP 适配器就是让 Codex 连接 Houdini 的那一层。整个链路是这样的：

1. Houdini 里运行 `Blib Bridge` shelf server。
2. Codex 在本地启动 `scripts\cli\blib_hou_mcp.py` 这个 MCP server。
3. 这个 MCP server 读取当前 Houdini bridge session，然后把 Houdini 工具暴露给
   Codex。

先确认 Houdini 已经打开，并且 shelf 上的 `Bridge` 已经启动。然后在这个仓库里运行：

```powershell
python scripts\cli\blib_hou_mcp.py --status
```

如果返回里的 `readiness.status` 是 `ready` 或 `degraded`，说明 MCP 适配器已经能
看到 Houdini bridge。

#### 添加到 Codex

Codex 使用 TOML 配置。这个仓库可以直接打印 Codex 可用的配置块：

```powershell
python scripts\cli\blib_hou_mcp.py --print-codex-config
```

把输出粘贴到 Codex 配置文件：

```text
%UserProfile%\.codex\config.toml
```

它看起来会像这样：

```toml
[mcp_servers.blib-houdini-bridge]
command = "C:\\Path\\To\\python.exe"
args = [
  "C:\\Path\\To\\houdini-mcp-cli\\scripts\\cli\\blib_hou_mcp.py",
]
```

然后重启 Codex，或者开一个新的 Codex 会话。如果你用的是 Codex CLI，也可以用下面
的命令确认 Codex 是否加载到了这个 server：

```powershell
codex mcp list
```

在 Codex 里可以这样问：

```text
Use the blib-houdini-bridge MCP server to read houdini://adapter/status,
then take a read-only scene snapshot of /obj.
```

如果连接成功，Codex 应该能看到这些资源或工具：

- `houdini://adapter/status`
- `houdini://scene/current`
- `houdini_scene_snapshot`
- `houdini_node_info`
- `houdini_edit_mode`

其他使用 JSON 配置的 MCP 客户端可以用：

```powershell
python scripts\cli\blib_hou_mcp.py --print-config
```

MCP 接口细节见 [docs/HOUDINI_MCP.md](docs/HOUDINI_MCP.md)。

### 安全设计

这个 bridge 的目标不是让外部工具随便改场景，而是让控制过程尽量可见、可审查：

- 读取命令不需要开启 edit mode
- 写入命令需要在 Houdini 侧明确开启 edit mode
- 工作流命令支持 review、validate、run、verify
- session token 只用于本地连接，不要贴到日志或提示词里

发布检查、交付流程和更完整的验证说明见
[docs/BRIDGE_ONLY_RELEASE.md](docs/BRIDGE_ONLY_RELEASE.md)。
