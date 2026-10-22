# 中文遥测服务

OpenObserve 提供简体中文界面和 trace 存储；OpenTelemetry Collector 使用标准 OTLP 接口接收插件追踪，再通过 Basic Auth 转发到 OpenObserve。服务位于独立 `telemetry/` 目录，插件自身不依赖 Docker。

```bash
# 从仓库根目录启动，不加载业务连接 .env。
docker compose --env-file /dev/null -f telemetry/compose.yml up -d
```

界面地址：`http://127.0.0.1:33047`。本机初始账号为 `admin@example.com`，密码为 `LocalTrace123!`。首次启动前可设置 `OPENOBSERVE_ROOT_EMAIL` 和 `OPENOBSERVE_ROOT_PASSWORD`；两项同时供服务初始化和 Collector 认证使用，已初始化账号的密码须通过 OpenObserve 管理。所有外部端口只绑定本机回环地址。

| 用途 | 外部端口 | 接收服务内部端口 |
| --- | --- | --- |
| OpenObserve 中文界面与查询 API | 33047 | 5080 |
| OTLP/gRPC | 33048 | 4317 |
| OTLP/HTTP | 33049 | 4318 |

镜像固定为 OpenObserve `v1.1.2`、Collector Contrib `0.123.0`，通过国内镜像源拉取。OpenObserve 使用命名数据卷保存本机数据，保留 7 天；自身产品使用遥测关闭。Collector 的标准 batch、memory_limiter 和内存重试队列负责缓冲转发，重启不保证尚未转发的事件保留；插件本地 JSONL trace 仍独立保存。

## 中文界面与查询

浏览器首选语言为简体中文时，界面自动选择简体中文；也可通过界面的语言菜单切换到“简体中文”。选择默认组织 `default`，进入链路追踪页面，数据流为 `dy_superpowers`。工具主 span 直接以工具名显示，展开后可看到实际执行子 span；展开事件查看记忆和调研正文、搜索词及查询条件。

## 项目与执行链路

通过基础参数检查的 MCP 工具调用创建独立的 OTel trace 和真实根 span；工具参数校验通过后，实际执行创建子 span。不同调用不制造未导出的父节点，也不以父子关系表示并不存在的调用嵌套。跨轮调研、补问和巡检使用相同的调用参数 `trace_id`，生成稳定的 `dy.workflow_id`；工作区参与散列，相同流程名不会串到其他项目。这里的参数 `trace_id` 是插件流程编号，不能直接当作 OpenObserve 的 32 位 OTel trace ID 查询。

| 标识/字段 | 用途 |
| --- | --- |
| `dy.workspace_id` | 工作区分组；初始化会话时返回，不导出目录路径或名称 |
| `dy.workflow_id` | 当前工作区中同一轮或跨会话任务的多次调用 |
| `dy.call_id` | 单次调用；对应本地事件的 `span_id` |
| `dy.span_role` | `tool` 为工具主 span，`operation` 为实际执行子 span |
| `dy.category` / `dy.category_label` | 稳定分类与中文分类名称 |
| `dy.check_key` / `dy.scope_id` / `dy.revision` | 检查项、适用范围与调研版本 |
| `dy.status` / `dy.error_code` / `dy.duration_ms` | 工具执行状态、错误码和毫秒耗时 |
| `dy.backend_status` | 记忆后端状态；`fallback` 表示降级，不等于工具失败 |
| `dy.result` / `dy.valid` / `dy.error_count` | 巡检业务门禁、记录校验结果和校验问题数量 |

正常完成明确标记 OTel `OK`，工具异常标记 `ERROR`。巡检返回“待确认”或 `valid=false` 时，工具本身仍可能正常执行，不能将其统计为程序异常。自报业务事件的结果属于调用方声明，不证明业务验证真实发生。

本地 JSONL 的 `otel_trace_id` 和 `otel_span_id` 可定位实际远程链路及节点；远程导出关闭或失败时，本地流程标识仍可使用。导出失败时不保证 OpenObserve 收到事件，也没有从本地日志自动补传的机制。

巡检调用任意工具时可附带以下可选字段；旧调用方式继续有效：

```json
{
  "trace_id": "expense-inspection-001",
  "name": "profile.md",
  "trace_context": {
    "skill": "expense-inspection",
    "module": "expense",
    "check_order": "1",
    "research_id": "01",
    "scope_id": "scope-a",
    "revision": "example-revision"
  }
}
```

示例对应 `dy_memory_read`。各标识只能使用字母、数字和 `_.:/#-`，填写实际读取到的 `scope_id`、`revision`，不填写中文描述、路径或正文。`module + check_order + research_id` 齐全时自动生成检查键。关联信息仅用于追踪，不会替代工具的业务参数、校验或确认要求；调研工具输出的实际范围、版本优先于调用方附带的标签。

## 查询与统计

插件属性 `dy.workspace_id` 在 OpenObserve 中存储为字段 `dy_workspace_id`，按工作区筛选的 SQL：

```sql
SELECT * FROM "dy_superpowers"
WHERE dy_workspace_id = '<工作区标识>'
  AND dy_span_role = 'tool'
ORDER BY _timestamp DESC
```

按工作区与工具统计调用次数：

```sql
SELECT dy_workspace_id, dy_tool, count(*) AS calls
FROM "dy_superpowers"
WHERE dy_span_role = 'tool'
GROUP BY dy_workspace_id, dy_tool
```

统计时只选择 `dy_span_role = 'tool'`，避免主 span 和子 span 重复计数。OpenObserve 将属性中的点号规范化为下划线；旧数据没有新字段，以下查询用于升级后数据。

按工作区回看一个多轮任务：

```sql
SELECT _timestamp, operation_name, trace_id, dy_check_key, dy_scope_id,
       dy_revision, dy_status, dy_result, dy_duration_ms
FROM "dy_superpowers"
WHERE dy_workspace_id = '<工作区标识>'
  AND dy_workflow_id = '<流程标识>' AND dy_span_role = 'tool'
ORDER BY _timestamp ASC
```

查看异常或超过 2 秒的调用：

```sql
SELECT _timestamp, trace_id, operation_name, dy_error_code, dy_duration_ms
FROM "dy_superpowers"
WHERE dy_workspace_id = '<工作区标识>' AND dy_span_role = 'tool'
  AND (dy_status = 'error' OR CAST(dy_duration_ms AS DOUBLE) > 2000)
ORDER BY _timestamp DESC
```

查看某个检查项的所有调研、记忆及 Odoo 操作：

```sql
SELECT _timestamp, trace_id, operation_name, dy_scope_id, dy_revision,
       dy_model, dy_domain, dy_result
FROM "dy_superpowers"
WHERE dy_workspace_id = '<工作区标识>' AND dy_span_role = 'tool'
  AND dy_check_key = 'expense:1:01'
ORDER BY _timestamp ASC
```

可在 OpenObserve 保存这些查询，或作为其原生仪表盘与告警的依据。看板需分别统计工具错误、记忆后端降级和巡检待确认，不能用一个“失败数”合并三者。单次工具耗时不能代表包含顾问等待时间的整个交付任务耗时。

当前版本 OpenObserve 将 OTLP trace 属性中的数字和布尔值存为文本；对耗时、计数做范围筛选或聚合时先 `CAST(... AS DOUBLE)`，错误码用文本 `'-32602'` 筛选，记录校验失败用 `dy_valid = 'false'` 筛选。原生 `span_status` 为 `OK` 或 `ERROR`，与业务验收结果分开使用。

查询 API 为 `POST /api/default/_search?type=traces`，使用 OpenObserve 的 Basic Auth，提供微秒单位的 `query.start_time`、`query.end_time`、`query.sql` 与 `query.size`。记忆正文、调研正文和搜索词保留在主 span 的 `events` 中；工作区、工具、模块、检查键、范围、版本、结果、数量和脱敏 domain 提升为可筛选属性。事件正文搜索能力以 OpenObserve 当前事件存储和查询支持为准，不能等同于所有属性的全文索引。

## 插件接入

安装 `dy_superpowers` 后，插件 `.mcp.json` 会自动把 `DY_SUPERPOWERS_OTLP_TRACES_ENDPOINT`
设置为 `http://127.0.0.1:33049/v1/traces`，并把 `DY_SUPERPOWERS_TELEMETRY_SCOPE` 设置为 `plugin`。
MCP 服务只创建 `dy_superpowers` 自己的 tracer provider，因此 collector 只会收到该插件产生的 span，
不会收集 Codex 主进程或其他 MCP 服务的 trace。无需在插件配置 OpenObserve 登录凭据，Collector 在服务内部处理认证。
Collector 未启动时，插件仍使用本地 JSONL trace。

可用 `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` 指定其他 collector；该标准变量优先于安装清单默认值。
额外目标须通过 `DY_OTLP_ALLOWED_ORIGINS` 授权精确 origin。认证可用标准 `OTEL_EXPORTER_OTLP_TRACES_HEADERS`；
细节见[运行边界](../plugins/dy_superpowers/docs/runtime-boundaries.md)。

记忆读写正文、调研内容、记忆搜索词和 Odoo domain 会进入本地 trace 及配置的 collector；密码、令牌及常见个人字段值脱敏，Odoo 返回行不记录。单条正文最多 64 KiB，超出有截断标记。

## 端到端验证

```bash
export DY_TEST_OPENOBSERVE_URL=http://127.0.0.1:33047
export DY_TEST_OPENOBSERVE_USER=admin@example.com
# 密码从环境注入，不提交到仓库。
export DY_TEST_OPENOBSERVE_PASSWORD="$OPENOBSERVE_ROOT_PASSWORD"
uv run --frozen --project plugins/dy_superpowers/mcp-server pytest -q \
  plugins/dy_superpowers/mcp-server/tests/test_openobserve_e2e.py
```

记忆用例会通过真实 MCP stdio 读写临时工作区，再按工作区从 OpenObserve 查询并校验正文、脱敏、流程关联、版本、成功/异常状态和巡检门禁。真实 Odoo 用例还需提供 `ODOO_URL`、`DY_ODOO_ALLOWED_ORIGINS`、`ODOO_DB`、`ODOO_USERNAME`、`ODOO_PASSWORD`，仅执行受限只读查询。

从旧服务切换时先停止旧 Grafana/Tempo 容器，再启动本配置；保留旧数据卷用于回退，历史 trace 不会自动迁移到 OpenObserve。
