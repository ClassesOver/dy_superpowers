# 运行架构

插件由 Codex 插件清单（manifest）、技能入口、插件内 MCP 服务端、版本化参考资料和当前工作区记忆组成。MCP 服务集成在插件目录内，由 `.codex-plugin/plugin.json` 引用 `.mcp.json` 启动。遥测 collector 位于市场仓库外层 `telemetry/`，与插件包和 MCP 进程隔离。

一个工作区只对应一个项目。`.dy_superpowers/` 保存该项目的 `preferences.md`、`profile.md`、`delivery-register.md`、`decisions.md`、`skill-candidates.md` 和经确认的 `module-research.md`。这些文件不修改既有项目 Markdown。

MCP 服务端提供工作区记忆、调研记录、计划校验、Odoo 12 只读查询和运行追踪（trace），并承担 MCP 工具调用前后的参数检查与生命周期追踪。入口通过 `${PLUGIN_ROOT}` 定位插件代码，进程工作目录保留为当前项目工作区；因此 `.dy_superpowers/` 记忆按项目工作区隔离，不写入插件安装目录。具体工具边界见 [hooks.md](hooks.md)、[memory.md](memory.md) 和 [odoo-config-engine.md](odoo-config-engine.md)。

标准 MCP 启动命令为 `uv run --frozen --project <插件>/mcp-server python <插件>/mcp-server/src/server.py`。Codex 运行环境需提供 uv；依赖由插件内 `uv.lock` 固定，虚拟环境位于插件的 `mcp-server/.venv`，与工作区记忆隔离。首次准备依赖需要可访问的包源，离线部署应提前同步依赖。

Codex `SessionStart` 通过插件声明的 MCP hook 调用 `dy_session_initialize`，返回工作区记忆索引，不读取或注入记忆正文。Agent 按需使用 `dy_memory_read` 读取完整项目记忆，巡检 skill 按检查键读取调研记录。hook 失败只影响恢复提示，不改变记忆写入确认和 Odoo 只读边界。

本地操作追踪默认启用，写入 Codex 用户数据目录下按工作区散列隔离的 JSONL 文件。OpenTelemetry OTLP 导出默认启用，默认指向本机 Jaeger `http://127.0.0.1:33049/v1/traces`；标准环境变量可覆盖 collector。`OTEL_SDK_DISABLED=true` 可关闭外部导出，不影响本地追踪和插件工具。Basic Memory 作为现有 MCP 内部的可选搜索后端接入，细节见 [memory.md](memory.md)。Odoo 当前仅开放受限 XML-RPC 只读查询；配置写入、SQL 和代码生成不属于当前运行时能力。
