# dy_superpowers Codex 插件市场

这是一个 Codex 插件市场仓库，插件位于 `plugins/dy_superpowers/`，catalog 位于 `.agents/plugins/marketplace.json`。

```bash
codex plugin marketplace add <仓库路径>
```

安装后，在 Codex 插件市场中选择 `dy_superpowers`。仓库内的 `plugins/dy_superpowers/.codex-plugin/plugin.json` 是插件清单，`plugins/dy_superpowers/.mcp.json` 注册插件内 MCP 服务。

运行前安装 uv 并确保 Codex 可从 PATH 找到它。插件首次启动按 `uv.lock` 准备 Python 依赖，需要可用包源；详细说明见 [插件启动说明](plugins/dy_superpowers/README.md)。

catalog 中的插件 ID 为 `dy_superpowers`。MCP 服务随插件位于 `plugins/dy_superpowers/mcp-server/`，由插件 `.mcp.json` 注册；插件不使用外部遥测，运行追踪只写入本地 JSONL 文件。

## 插件校验

安装 Codex CLI 和 uv 后，在仓库根目录运行：

```bash
bash scripts/validate-plugin.sh
```

这是仓库提供的校验入口，调用 pytest 和官方 Codex CLI；依赖由 `uv.lock` 固定。它解析 MCP/hooks 配置、检查插件内部引用与技能入口，并在临时 `CODEX_HOME` 中执行市场注册、插件安装及启用状态检查。随后检查 hook 与工具注册的契约，并在临时工作区启动 MCP，验证握手、工具发现、记忆和调研读写。测试使用模拟 Odoo，不访问真实业务环境或修改日常 Codex 配置。缺少 `codex` 或 `uv` 时立即失败。

已核对 Codex CLI `0.163.0` 和官方公开的 [plugin-creator](https://github.com/openai/skills/tree/main/skills/.system/plugin-creator)：CLI 没有 `plugin validate` 子命令，该技能包也没有 `validate_plugin.py`。本仓库采用官方 CLI 的实际安装流程验证兼容性，不依赖不存在的脚本。此检查覆盖配置引用、市场安装、hook 契约和 MCP 协议冒烟，不代表真实 Odoo 环境或 Codex 会话 hook 的完整联调验收；插件能力说明见 [OpenAI 官方文档](https://developers.openai.com/codex/plugins/)。
