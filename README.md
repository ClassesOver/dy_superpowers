# dy_superpowers Codex 插件市场

这是一个 Codex 插件市场仓库，插件位于 `plugins/dy_superpowers/`，catalog 位于 `.agents/plugins/marketplace.json`。

```bash
codex plugin marketplace add <仓库路径>
```

安装后，在 Codex 插件市场中选择 `dy_superpowers`。仓库内的 `plugins/dy_superpowers/.codex-plugin/plugin.json` 是插件清单，`plugins/dy_superpowers/.mcp.json` 注册插件内 MCP 服务。

运行前安装 uv 并确保 Codex 可从 PATH 找到它。插件首次启动按 `uv.lock` 准备 Python 依赖，需要可用包源；详细说明见 [插件启动说明](plugins/dy_superpowers/README.md)。

catalog 中的插件 ID 为 `dy_superpowers`。遥测 collector 在 `telemetry/` 下单独运行，不属于插件包；MCP 服务随插件位于 `plugins/dy_superpowers/mcp-server/`，由插件 `.mcp.json` 注册。
