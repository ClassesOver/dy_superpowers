# dy_superpowers 插件

`dy_superpowers` 是面向 HRP 项目交付的 Codex 插件骨架，首期兼容基线为 Odoo 12。

当前实现提供：

- 插件清单和插件内 MCP 服务端注册；
- 当前工作区单项目 Markdown 记忆读写工具；
- 按检查键保存、读取并保留历史版本的模块调研记忆工具；
- 计划结构校验，以及声明范围内巡检覆盖、调研版本和验收依据校验工具；
- 插件运行追踪的结构化记录、脱敏和查询工具；
- OpenTelemetry OTLP trace 导出。
- MCP 工具调用前后生命周期 hook；
- Codex `SessionStart` 工作区记忆恢复 hook；

所有 MCP 工具调用都必须传入 `trace_id`；本地 JSONL 操作追踪和 OpenTelemetry OTLP 导出默认启用，默认 collector 为本机 Collector `http://127.0.0.1:33049/v1/traces`。每条 trace 都带 `dy.workspace_id`，由工作区绝对路径散列得到，不导出路径或目录名；OpenObserve 可用该属性按项目工作区筛选。工具 span 记录工具名、状态、耗时和错误码；记忆 trace 包含读取/写入正文、搜索词和搜索摘要，调研读写包含当前记录，Odoo 搜索包含 domain 条件及分页/字段计数。凭据和常见个人字段值脱敏，单条正文最多记录 64 KiB，超限会标记 `content_truncated`；Odoo 返回记录行不写入 trace。

每次工具调用生成完整的根 span 和执行子 span；跨轮任务按 `dy.workflow_id` 关联。可用可选 `trace_context` 将记忆和 Odoo 调用标记到模块、检查项、范围和调研版本。本地事件包含实际 OTel 关联标识；初始化会话返回工作区追踪标识。统计调用次数时只选择 `dy.span_role=tool`，并区分工具异常、后端降级与巡检待确认，查询示例见[遥测服务](../../telemetry/README.md)。

### 安装时自动接入遥测

插件的 `.mcp.json` 在安装后会自动向插件 MCP 进程注入本机 Collector：
`http://127.0.0.1:33049/v1/traces`，并设置 `DY_SUPERPOWERS_TELEMETRY_SCOPE=plugin`。该配置只影响
`dy_superpowers` 的 MCP 进程；不会给 Codex 或其他 MCP 服务安装全局 exporter。Collector 未启动、不可达或导出失败时，
本地 JSONL trace 和业务工具仍继续工作。

安装后如需指定其他 collector，启动 Codex 前设置标准环境变量即可覆盖插件默认值：

```bash
DY_OTLP_ALLOWED_ORIGINS=https://collector.example \
  OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=https://collector.example/v1/traces codex
```

也可以只对 MCP 进程设置：

```bash
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://127.0.0.1:33049/v1/traces \
  python3 plugins/dy_superpowers/mcp-server/src/server.py
```

优先级为 `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`，其次是 `OTEL_EXPORTER_OTLP_ENDPOINT`（自动补 `/v1/traces`），
再其次是安装清单注入的 `DY_SUPERPOWERS_OTLP_TRACES_ENDPOINT`，最后才使用内置默认地址。
远程端点仍须通过 `DY_OTLP_ALLOWED_ORIGINS` 授权精确 origin。遥测端点不可达时，插件仍保留本地 trace 并继续工作。
插件默认把单次 OTLP 导出等待限制为 1 秒（可用 `DY_SUPERPOWERS_OTLP_EXPORT_TIMEOUT_MS` 调整，范围 100–5000 毫秒），避免退出时等待不可达服务。

当前通过受限的 Odoo 12 JSON-RPC 只读工具连接真实 Odoo，不执行远程写操作、SQL 或附加模块代码生成。已加入模块多轮调研技能 `module-research`，以及两个 Odoo 12 HRP 交付巡检技能：`expense-inspection` 和 `general-ledger-inspection`；后续技能继续通过 `skills/<name>/SKILL.md` 加入，确定性的 Odoo 能力通过 MCP 工具加入。

Odoo 只读工具包括 `dy_odoo_record_search`、`dy_odoo_record_read`、`dy_odoo_model_fields`、`dy_odoo_module_list_installed` 和 `dy_odoo_module_info`。`dy_knowledge_module_info` 与 `dy_knowledge_search` 只提供本地模块解释，不替代实时账套事实或项目调研结论。

Odoo 连接必须同时配置 `ODOO_URL` 和 `DY_ODOO_ALLOWED_ORIGINS`，例如 `ODOO_URL=http://10.0.0.8:8069`、`DY_ODOO_ALLOWED_ORIGINS=http://10.0.0.8:8069`。允许列表为逗号分隔的精确 `scheme://host:port`，不能使用通配符或 URL 路径。数据库、账号和密码仍分别通过 `ODOO_DB`、`ODOO_USERNAME`、`ODOO_PASSWORD` 配置。搜索统一使用 `dy_odoo_record_search`。

本地日志每个工作区默认保留最近 30 个 UTC 自然日，总容量不超过 100 MiB；按 trace 和日期分文件，单文件达到 5 MiB 轮转，保留一份备份。追加和读取时执行清理；被清理的历史不再可查询。完整出站策略及边界见 [运行边界](docs/runtime-boundaries.md)。

Hook 边界见 [docs/hooks.md](docs/hooks.md)。

## 启动插件

先安装 uv 并确保 Codex 启动时可从 PATH 找到它。插件通过 `uv run --frozen --project` 使用随包分发的 `uv.lock` 安装依赖并启动；首次启动需要访问 Python 包源，后续使用本地环境。需要提前准备依赖时，在市场仓库根目录执行：

```bash
uv sync --frozen --project plugins/dy_superpowers/mcp-server
uv run --frozen --project plugins/dy_superpowers/mcp-server python plugins/dy_superpowers/mcp-server/src/server.py
```

Codex 通过 `.mcp.json` 的 `${PLUGIN_ROOT}` 定位插件和依赖项目，`--project` 不切换进程工作目录，默认以启动目录作为工作区，并向 stderr 提示；可通过 `DY_WORKSPACE_ROOT=/绝对项目路径` 或服务参数 `--workspace /绝对项目路径` 显式指定，命令行优先。启动时验证目录存在，并拒绝插件安装目录及其子目录。市场安装本身不安装 Python 依赖；标准启动在插件内创建 `.venv`，使用锁定的 MCP 2.x SDK stdio 传输；工具说明、schema 和处理器统一注册，参数错误与内部错误分别保留 `-32602`、`-32603`。缺少 MCP SDK 时会明确报错，不会切换到另一套协议实现。Collector 不可达时，外部导出不可用，但本地 trace 和工具调用仍可继续。

## 可选：独立遥测服务

遥测服务位于插件项目内隔离的 `telemetry/` 子目录，由 OpenObserve 提供简体中文查询界面与 trace 存储，OpenTelemetry Collector 接收并转发 OTLP trace；Codex 插件和插件内 MCP 服务不依赖 Docker。默认 OTLP 地址指向本机 Collector；未启动 collector 时，本地 JSONL trace 和插件功能仍可用。无需外部遥测时可设置 `OTEL_SDK_DISABLED=true`，这不会关闭本地 trace。

启动本机 OpenObserve + OpenTelemetry Collector：

```bash
docker compose --env-file /dev/null -f telemetry/compose.yml up -d
```

OpenObserve 位于 `http://127.0.0.1:33047`，本机初始账号为 `admin@example.com`，密码为 `LocalTrace123!`；启动前可用 `OPENOBSERVE_ROOT_EMAIL` / `OPENOBSERVE_ROOT_PASSWORD` 覆盖。界面支持简体中文，可在语言菜单中选择。OTLP/gRPC 外部端口为 `33048`，OTLP/HTTP 外部端口为 `33049`；OpenObserve 保留 7 天 trace。本地 trace 包含用户确认保存的记忆正文及查询内容。远程接入其他 OTLP collector 前，先用 `DY_OTLP_ALLOWED_ORIGINS` 授权精确 origin，再通过 `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` 覆盖默认地址；认证使用标准变量 `OTEL_EXPORTER_OTLP_TRACES_HEADERS` 或 `OTEL_EXPORTER_OTLP_HEADERS`。远程 collector 会收到允许记录的记忆正文和 Odoo 查询条件。

## 工作区记忆

单个工作区只对应一个项目。项目记忆必须位于工作区的 `.dy_superpowers/`，不得读取或写入其他工作区的记忆。

插件只注册一个 MCP Server：`dy-superpowers-odoo`。Basic Memory `0.23.2` 是必需依赖，由插件启动时通过 `uv.lock` 一并安装，运行环境要求 Python 3.12 及以上；无需单独安装全局 CLI。现有 MCP Server 的 `dy_memory_search` 调用同一 Python 环境中的 CLI 建立当前工作区索引。运行故障时回退本地 Markdown 搜索，并明确返回降级状态；降级不代表依赖可省略。目录隔离规则见 [docs/memory.md](docs/memory.md)。

调研修订与范围停用使用 `expected_revision` 和稳定 `scope_id`；普通记忆更新使用 `expected_content` 防止覆盖其他会话的新内容。巡检结果使用 `dy_inspection_validate` 检查声明范围内的覆盖与证据链，详见 [巡检校验合同](docs/inspection-validation.md)。校验不验证证据真实性，不授予业务或记忆写入权限。
