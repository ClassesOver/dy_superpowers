# dy_superpowers 插件

`dy_superpowers` 是面向 HRP 项目交付的 Codex 插件骨架，首期兼容基线为 Odoo 12。

当前实现提供：

- 插件清单和插件内 MCP 服务端注册；
- 当前工作区单项目 Markdown 记忆读写工具；
- 按检查键保存、读取并保留历史版本的模块调研记忆工具；
- 计划结构校验工具；
- 插件运行追踪的结构化记录、脱敏和查询工具；
- OpenTelemetry OTLP trace 导出。
- MCP 工具调用前后生命周期 hook；
- Codex `SessionStart` 工作区记忆恢复 hook；

所有 MCP 工具调用都必须传入 `trace_id`；本地 JSONL 操作追踪和 OpenTelemetry OTLP 导出默认启用，默认 collector 为本机 Jaeger `http://127.0.0.1:33049/v1/traces`。标准环境变量 `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` 或 `OTEL_EXPORTER_OTLP_ENDPOINT` 可覆盖 collector；设置 `OTEL_SDK_DISABLED=true` 可关闭外部遥测而不影响插件本地功能。导出事件只包含白名单属性；记忆读写记录操作类型、状态和文件名，不记录 Markdown 正文。

### 安装或启动时指定遥测端点

marketplace 安装不会修改端点。启动 Codex 前设置标准环境变量即可覆盖默认值：

```bash
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=https://collector.example/v1/traces codex
```

也可以只对 MCP 进程设置：

```bash
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://127.0.0.1:33049/v1/traces \
  python3 plugins/dy_superpowers/mcp-server/src/server.py
```

优先级为 `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`，其次是 `OTEL_EXPORTER_OTLP_ENDPOINT`（自动补 `/v1/traces`），最后才使用内置默认地址。遥测端点不可达时，插件仍保留本地 trace 并继续工作。

当前通过受限的 Odoo 12 XML-RPC 只读工具连接真实 Odoo，不执行远程写操作、SQL 或附加模块代码生成。已加入模块多轮调研技能 `module-research`，以及两个 Odoo 12 HRP 交付巡检技能：`expense-inspection` 和 `general-ledger-inspection`；后续技能继续通过 `skills/<name>/SKILL.md` 加入，确定性的 Odoo 能力通过 MCP 工具加入。

Odoo 只读工具包括 `dy_odoo_record_search`、`dy_odoo_record_read`、`dy_odoo_model_fields`、`dy_odoo_module_list_installed` 和 `dy_odoo_module_info`。`dy_knowledge_module_info` 与 `dy_knowledge_search` 只提供本地模块解释，不替代实时账套事实或项目调研结论。

Hook 边界见 [docs/hooks.md](docs/hooks.md)。

## 启动插件

安装 MCP 服务端依赖并从插件目录启动：

```bash
python3 -m pip install -e plugins/dy_superpowers/mcp-server
python3 plugins/dy_superpowers/mcp-server/src/server.py
```

Codex 通过 `.mcp.json` 的 `${PLUGIN_ROOT}` 路径启动插件代码，进程工作目录维持为当前项目工作区，因此项目记忆始终写入该工作区。服务优先使用 MCP Python SDK 的 stdio 传输；若 SDK 不可用，会运行内置的 stdio JSON-RPC 兼容入口。OpenTelemetry 依赖未安装或 Jaeger 不可达时，外部导出不可用，但本地 trace 和工具调用仍可继续。

## 可选：独立遥测服务

遥测服务位于插件项目内隔离的 `telemetry/` 子目录，只负责 Jaeger/collector；Codex 插件和插件内 MCP 服务不依赖 Docker。默认 OTLP 地址指向本机 Jaeger；未启动 collector 时，本地 JSONL trace 和插件功能仍可用。无需外部遥测时可设置 `OTEL_SDK_DISABLED=true`，这不会关闭本地 trace。

需要本机查询界面时启动 Jaeger：

```bash
docker compose -f telemetry/compose.yml up -d
```

Jaeger UI 位于 `http://127.0.0.1:33047`。OTLP/gRPC 外部端口为 `33048`，OTLP/HTTP 外部端口为 `33049`。远程接入 Grafana Tempo 或其他 OTLP/HTTP collector 时，通过 `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` 覆盖默认地址；认证使用标准变量 `OTEL_EXPORTER_OTLP_TRACES_HEADERS` 或 `OTEL_EXPORTER_OTLP_HEADERS`。远程事件不包含工作区路径、记忆正文、证据引用或业务记录。

## 工作区记忆

单个工作区只对应一个项目。项目记忆必须位于工作区的 `.dy_superpowers/`，不得读取或写入其他工作区的记忆。

插件只注册一个 MCP Server：`dy-superpowers-odoo`。可选安装 Basic Memory `0.23.2` 后，现有 MCP Server 的 `dy_memory_search` 会调用其 CLI 建立当前工作区索引。Basic Memory 不可用时自动回退本地 Markdown 搜索，不影响插件其他功能。安装方式和目录隔离规则见 [docs/memory.md](docs/memory.md)。
