# Trace 与项目模块调研记忆优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `dy_superpowers` 在 Codex MCP 调用中复用宿主 Trace，同时把调研清单驱动的多轮问答按“当前工作区项目 → 模块 → 检查子项”保存、恢复和审计。

**Architecture:** Codex 的 `mcp.tools.call` 是外层 Trace；插件从 MCP `_meta.traceparent/tracestate` 接收合法的 W3C 父上下文，并在其下创建工具 span 和业务操作子 span。没有宿主上下文时，插件创建自己的根 span。工作区是唯一项目边界，项目事实只落在 `.dy_superpowers/modules/<module>/research.md`，每个模块独立维护检查子项、适用范围和版本历史。模块调研 skill 以插件内巡检清单为问题目录，允许跨会话逐项提问，只有用户确认后的答案才写入模块记忆；所有模块记忆查看都通过专用 MCP 工具完成。巡检校验通过后，由专用报告工具把固定结构的中文报告写入 `.dy_superpowers/reports/inspection/<module>/`。

**Tech Stack:** Python 3.12、MCP Python SDK、OpenTelemetry API/SDK、Basic Memory 0.23.2、pytest、Markdown/JSON 记录、Odoo 12 只读 JSON-RPC。

**Spec:** `plugins/dy_superpowers/docs/architecture.md`、`plugins/dy_superpowers/docs/memory.md`、`plugins/dy_superpowers/docs/runtime-boundaries.md`、`plugins/dy_superpowers/skills/module-research/SKILL.md`、`plugins/dy_superpowers/skills/expense-inspection/SKILL.md`、`plugins/dy_superpowers/skills/general-ledger-inspection/SKILL.md`。

## Global Constraints

- 一个工作区只对应一个项目；项目标识由工作区稳定哈希生成，任何调研读取、写入和 Basic Memory 索引都不得跨工作区。
- 调研问题必须来自对应模块巡检清单；清单中的“回答参考”、示例和“待确认”不是项目事实。
- 多轮问答允许跨调用、跨会话继续；未确认答案只能留在当前交互上下文，确认后的结论才写入模块记忆。
- 模块调研记录必须带 `module`、矩阵原始 `check_order`、`research_id`、`scope_id`、`revision`、依据、确认人和确认日期；只有 `confirmed` 可驱动巡检。
- 模块调研只使用 `.dy_superpowers/modules/<module>/research.md`；不读取、迁移或回退到旧的 `.dy_superpowers/module-research.md`。
- 模块记忆查看必须通过 `dy_module_memory_read`，工具内部执行路径校验、范围过滤和 Trace；skill 不直接读取工作区文件。
- 巡检报告必须先通过 `dy_inspection_validate`，再由 `dy_inspection_report_write` 在用户确认后写入 `.dy_superpowers/reports/inspection/<module>/`；校验失败不得落盘。
- 记忆只写当前工作区 `.dy_superpowers/`；不得覆盖插件内参考矩阵；密码、令牌、员工个人信息和 Odoo 返回行不得写入记忆或 Trace。
- 每次 MCP 工具调用保留本地 JSONL Trace；OTLP 初始化、连接、导出或远程服务失败不得阻断工具功能。
- Codex 有合法父上下文时插件 span 必须与父 span 共用 `trace_id`，并把插件工具 span 的 `parent.span_id` 指向 Codex MCP span；无父上下文时插件 span 才是根 span。
- 新测试使用 pytest，不使用 `unittest`；修改 manifest 或 MCP 注册后运行仓库约定的验证脚本。
- Odoo 12 连接只读，继续复用已有客户端缓存和参数校验，不增加写操作或通用 RPC。

## 文件边界与职责

- 修改 `plugins/dy_superpowers/mcp-server/src/dy_superpowers_mcp/telemetry.py`：解析外部 W3C 上下文，提供父子 span 创建和安全回退。
- 修改 `plugins/dy_superpowers/mcp-server/src/server.py`：接收低级 JSON-RPC 和 Python MCP SDK 两条路径的 `_meta`，把父上下文传到 `execute_tool`，统一错误和生命周期记录。
- 修改 `plugins/dy_superpowers/mcp-server/src/dy_superpowers_mcp/research.py`：按模块定位唯一记录文件，保留版本/范围/修复语义，拒绝旧扁平文件。
- 修改 `plugins/dy_superpowers/mcp-server/src/dy_superpowers_mcp/memory.py`：提供项目模块记忆路径、模块标识校验和允许文件边界。
- 创建 `plugins/dy_superpowers/mcp-server/src/dy_superpowers_mcp/reporting.py`：校验巡检报告、渲染固定中文 Markdown 并安全写入固定目录。
- 修改 `plugins/dy_superpowers/mcp-server/src/dy_superpowers_mcp/basic_memory.py`：索引当前工作区所有模块记忆，确保搜索结果带模块路径并拒绝跨项目路径。
- 修改 `plugins/dy_superpowers/mcp-server/tests/test_trace_integration.py`：覆盖宿主父 span、插件根 span、无效上下文、业务上下文隔离和降级。
- 创建 `plugins/dy_superpowers/mcp-server/tests/test_research_module_memory.py`：覆盖项目/模块隔离、多轮确认写入、模块记忆工具读取和冲突修订。
- 创建 `plugins/dy_superpowers/mcp-server/tests/test_inspection_report.py`：覆盖报告门禁、固定目录、固定结构、确认写入和敏感内容过滤。
- 修改 `plugins/dy_superpowers/mcp-server/tests/test_session_initialize.py`：覆盖初始化返回模块清单索引、已确认键和待办键，不注入未确认答案。
- 修改 `plugins/dy_superpowers/skills/module-research/SKILL.md`：定义清单驱动的初始化和多轮调研状态机，以及模块记忆路径。
- 修改 `plugins/dy_superpowers/skills/expense-inspection/SKILL.md`、`plugins/dy_superpowers/skills/general-ledger-inspection/SKILL.md`：改为按模块读取调研记忆，并在补充调研结束后回到原检查子项。
- 修改 `plugins/dy_superpowers/docs/architecture.md`、`plugins/dy_superpowers/docs/memory.md`、`plugins/dy_superpowers/docs/runtime-boundaries.md`、`telemetry/README.md`：记录最终边界、字段和查询方法。

---

### Task 1: 建立项目/模块调研记忆模型

**Files:**
- Modify: `plugins/dy_superpowers/mcp-server/src/dy_superpowers_mcp/memory.py`
- Modify: `plugins/dy_superpowers/mcp-server/src/dy_superpowers_mcp/research.py`
- Create: `plugins/dy_superpowers/mcp-server/tests/test_research_module_memory.py`

**Interfaces:**
- Produces `module_memory_path(workspace: Path, module: str) -> Path`，结果固定为 `<workspace>/.dy_superpowers/modules/<module>/research.md`。
- Produces `module_history_path(workspace: Path, module: str, revision: str) -> Path`，结果只能位于该模块的 `history/` 子目录。
- 保持 `read_record(workspace, module, check_order, research_id, *, scope=None, scope_id=None)` 和 `write_record(workspace, data)` 的外部参数不变；内部读写改为模块文件。

- [ ] **Step 1: 写失败测试，验证项目和模块物理隔离**

```python
def research_arguments(**updates):
    value = {
        "module": "expense", "check_order": "EXP-1-001", "research_id": "01",
        "config_item": "费用应用", "conclusion": "已确认", "scope": "本期",
        "confirmed_by": "顾问", "confirmed_at": "2026-10-22", "confirmed": True,
    }
    value.update(updates)
    return value


def test_research_records_are_partitioned_by_project_and_module(tmp_path):
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    write_record(project_a, research_arguments(module="expense", conclusion="费用已确认"))
    write_record(project_a, research_arguments(module="general-ledger", conclusion="总账已确认"))

    assert (project_a / ".dy_superpowers/modules/expense/research.md").is_file()
    assert (project_a / ".dy_superpowers/modules/general-ledger/research.md").is_file()
    assert read_record(project_a, "expense", "EXP-1-001", "01")["conclusion"] == "费用已确认"
    assert read_record(project_b, "expense", "EXP-1-001", "01") is None
```

- [ ] **Step 2: 运行测试，确认当前实现未按模块分区**

运行：`PYTHONPATH=plugins/dy_superpowers/mcp-server/src python3 -m pytest -q plugins/dy_superpowers/mcp-server/tests/test_research_module_memory.py::test_research_records_are_partitioned_by_project_and_module`

预期：失败，因为当前实现还没有把记录写入模块专属目录。

- [ ] **Step 3: 实现模块路径和标识校验**

在 `memory.py` 中增加只接受 `^[a-z0-9][a-z0-9_-]{0,63}$` 的模块路径函数；在 `research.py` 中只允许模块路径函数返回的 `research.md`。归档快照写入当前模块目录，所有 `resolve()` 结果必须仍位于当前工作区。

- [ ] **Step 4: 拒绝旧扁平记忆文件**

模块文件不存在时返回“当前模块尚未调研”，不读取 `.dy_superpowers/module-research.md`，不自动迁移、不合并同名记录。发现旧扁平文件时只返回明确的迁移提示错误，要求人工按新的模块清单重新确认和写入；该文件不能被巡检或 Basic Memory 索引。

- [ ] **Step 5: 运行测试，确认隔离和版本语义通过**

运行：`PYTHONPATH=plugins/dy_superpowers/mcp-server/src python3 -m pytest -q plugins/dy_superpowers/mcp-server/tests/test_research_module_memory.py plugins/dy_superpowers/mcp-server/tests/test_research_lifecycle.py`

预期：PASS；同一检查键在不同模块、不同工作区和不同 `scope_id` 下互不覆盖，旧扁平文件不会被读取或索引。

- [ ] **Step 6: 提交独立变更**

```bash
git add plugins/dy_superpowers/mcp-server/src/dy_superpowers_mcp/memory.py \
  plugins/dy_superpowers/mcp-server/src/dy_superpowers_mcp/research.py \
  plugins/dy_superpowers/mcp-server/tests/test_research_module_memory.py
git commit -m "feat: partition research memory by project and module"
```

### Task 2: 让模块巡检清单驱动初始化和多轮调研

**Files:**
- Modify: `plugins/dy_superpowers/mcp-server/src/server.py`
- Modify: `plugins/dy_superpowers/skills/module-research/SKILL.md`
- Modify: `plugins/dy_superpowers/skills/expense-inspection/SKILL.md`
- Modify: `plugins/dy_superpowers/skills/general-ledger-inspection/SKILL.md`
- Modify: `plugins/dy_superpowers/mcp-server/tests/test_session_initialize.py`
- Modify: `plugins/dy_superpowers/mcp-server/tests/test_research_module_memory.py`

**Interfaces:**
- `dy_session_initialize` 返回中文文本；文本包含模块清单索引行、`module`、清单引用、清单摘要哈希、已确认键和待办键，不返回整份记忆正文。
- `dy_module_memory_read(trace_id, module, check_order=None, research_id=None, scope=None, scope_id=None, offset=0, limit=100)` 是模块调研记忆的唯一查看工具；提供检查键时返回一条当前记录，未提供时按分页返回该模块已确认/已停用记录及版本元数据。
- `dy_research_record_read` 从公开 MCP 工具中移除；内部校验代码直接调用受路径保护的 `read_record`，skill 统一改用 `dy_module_memory_read`。
- 多轮会话状态只保存已确认的事实和用户确认后的待办；未确认答案不调用写入工具，不进入 Trace 正文。

- [ ] **Step 1: 写失败测试，验证模块记忆工具和初始化清单状态**

```python
import server
from dy_superpowers_mcp.research import write_record


def write_confirmed_record(workspace, *, module, conclusion):
    write_record(workspace, {
        "module": module, "check_order": "1", "research_id": "01",
        "config_item": "模块配置", "conclusion": conclusion, "scope": "本期",
        "confirmed_by": "顾问", "confirmed_at": "2026-10-22", "confirmed": True,
    })


def test_module_memory_read_is_the_only_module_memory_view(tmp_path):
    write_confirmed_record(tmp_path, module="expense", conclusion="费用已确认")
    write_confirmed_record(tmp_path, module="general-ledger", conclusion="总账已确认")

    value, error = server.execute_tool(
        "dy_module_memory_read",
        {"trace_id": "module-view", "module": "expense"},
        tmp_path,
    )

    assert error is None
    assert value["module"] == "expense"
    assert all(item["module"] == "expense" for item in value["records"])
    assert "总账已确认" not in str(value)


def test_session_initialize_lists_module_checklist_state(tmp_path):
    value, error = server.execute_tool(
        "dy_session_initialize",
        {"trace_id": "init-research", "source": "startup"},
        tmp_path,
    )
    assert error is None
    assert "模块调研清单" in value
    assert "module=expense" in value
    assert "confirmed_keys=" in value
    assert "pending_keys=" in value
    assert "conclusion" not in value
```

- [ ] **Step 2: 运行测试，确认当前工具和初始化契约不满足要求**

运行：`PYTHONPATH=plugins/dy_superpowers/mcp-server/src python3 -m pytest -q plugins/dy_superpowers/mcp-server/tests/test_research_module_memory.py::test_module_memory_read_is_the_only_module_memory_view plugins/dy_superpowers/mcp-server/tests/test_session_initialize.py::test_session_initialize_lists_module_checklist_state`

预期：失败，因为当前没有 `dy_module_memory_read` 工具，初始化也只返回基础提示文本。

- [ ] **Step 3: 实现模块记忆查看工具**

在 `server.py` 注册 `dy_module_memory_read`，参数必须包含 `trace_id` 和合法 `module`，可选检查键、范围、分页。工具只调用 `module_memory_path` 和结构化记录读取器，不接受任意文件名或路径；每次读取记录 `memory.read` 事件以及模块、检查键、范围和修订版本。没有记录时返回空列表和 `found=false`，不能把缺失转换为未确认结论。

- [ ] **Step 4: 实现清单索引和状态汇总**

初始化时从插件内已安装的模块 skill 参考清单读取模块名称、矩阵路径和摘要哈希；从当前工作区模块记忆读取已确认检查键；从 `delivery-register.md` 读取用户确认保存的待办键。将这些信息追加为稳定的中文索引行，保留现有文本返回契约；不返回调研正文、候选答案或敏感字段。不存在项目记忆时返回空集合，不创建空模块文件。

- [ ] **Step 5: 固化多轮调研状态机**

在 `module-research/SKILL.md` 写明：初始化按清单建立模块调研游标；每轮先调用 `dy_module_memory_read` 检查当前记录；只提出当前最小问题；答案整理为候选结论；向用户确认；确认后按一个 `module/check_order/research_id/scope_id` 写入；未确认时只在会话中保持 `pending`；跨会话需用户确认保存待办后写入 `delivery-register.md`。巡检只恢复 `confirmed` 键，其他键保持阻塞。

- [ ] **Step 6: 更新两个巡检 skill 的路径和回链**

将模块记忆读取说明统一改为调用 `dy_module_memory_read`，底层文件固定为 `.dy_superpowers/modules/<module>/research.md`；要求每个调用保留模块、原始检查顺序、子项编号、范围和版本；补充调研完成后按原 `trace_id` 回到被阻塞子项，不把整份模块记忆加载进上下文。

- [ ] **Step 7: 运行测试并检查中文文档**

运行：`PYTHONPATH=plugins/dy_superpowers/mcp-server/src python3 -m pytest -q plugins/dy_superpowers/mcp-server/tests/test_session_initialize.py plugins/dy_superpowers/mcp-server/tests/test_research_module_memory.py`

预期：PASS；静态检查确认没有把模板答案、未确认答案或正文注入初始化返回值。

- [ ] **Step 8: 提交初始化和 skill 变更**

```bash
git add plugins/dy_superpowers/mcp-server/src/server.py \
  plugins/dy_superpowers/skills/module-research/SKILL.md \
  plugins/dy_superpowers/skills/expense-inspection/SKILL.md \
  plugins/dy_superpowers/skills/general-ledger-inspection/SKILL.md \
  plugins/dy_superpowers/mcp-server/tests/test_session_initialize.py \
  plugins/dy_superpowers/mcp-server/tests/test_research_module_memory.py
git commit -m "feat: drive multi-turn research from module checklists"
```

### Task 3: 解析 Codex MCP 父 Trace 上下文

**Files:**
- Modify: `plugins/dy_superpowers/mcp-server/src/dy_superpowers_mcp/telemetry.py`
- Create: `plugins/dy_superpowers/mcp-server/tests/test_trace_parent_context.py`

**Interfaces:**
- `extract_parent_context(meta: object) -> object | None`：只接受 `_meta` 中的字符串 `traceparent` 和可选 `tracestate`，返回 OpenTelemetry `Context`；非法、缺失或不完整值返回 `None`。
- `tool_span(..., parent_trace_context: dict[str, str] | None = None)`：有合法父上下文时建立子 span，无父上下文时建立插件根 span。
- 业务 `trace_context` 仍只生成属性，不能伪造 OTel 父身份。

- [ ] **Step 1: 写父子链路失败测试**

```python
def build_in_memory_provider(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(telemetry, "_tracer", provider.get_tracer("trace-test"))
    return provider, exporter


def test_plugin_span_uses_codex_parent_trace(monkeypatch):
    provider, exporter = build_in_memory_provider(monkeypatch)
    codex = provider.get_tracer("codex-test")
    with codex.start_as_current_span("mcp.tools.call") as parent:
        with telemetry.tool_span("flow", "/tmp/project", "dy_memory_read",
                                 parent_trace_context={
                                     "traceparent": f"00-{parent.get_span_context().trace_id:032x}-{parent.get_span_context().span_id:016x}-01"
                                 }):
            pass
    spans = exporter.get_finished_spans()
    plugin = next(item for item in spans if item.name == "dy_memory_read")
    assert plugin.context.trace_id == parent.get_span_context().trace_id
    assert plugin.parent.span_id == parent.get_span_context().span_id
```

- [ ] **Step 2: 写无效上下文和根 span 测试**

```python
def test_invalid_parent_context_falls_back_to_plugin_root(monkeypatch):
    provider, exporter = build_in_memory_provider(monkeypatch)
    with telemetry.tool_span("flow", "/tmp/project", "dy_memory_read",
                             parent_trace_context={"traceparent": "invalid"}):
        pass
    span = exporter.get_finished_spans()[0]
    assert span.parent is None
```

- [ ] **Step 3: 运行测试，确认当前实现强制创建根节点**

运行：`PYTHONPATH=plugins/dy_superpowers/mcp-server/src python3 -m pytest -q plugins/dy_superpowers/mcp-server/tests/test_trace_parent_context.py`

预期：父子断言失败，说明当前 `tool_span` 使用空 `Context()`。

- [ ] **Step 4: 实现 W3C 上下文提取和安全回退**

用 OpenTelemetry propagator 从只包含 `traceparent/tracestate` 的临时 carrier 提取 Context；不接受 `trace_id`、`span_id`、业务 `trace_context` 或任意 MCP 参数作为父身份。`_span` 只有在传入合法父 Context 时传 `context=...`；`root=True` 只用于没有父 Context 的情况。解析异常写入 stderr，不影响工具调用。

- [ ] **Step 5: 运行 Trace 单元测试**

运行：`PYTHONPATH=plugins/dy_superpowers/mcp-server/src python3 -m pytest -q plugins/dy_superpowers/mcp-server/tests/test_trace_parent_context.py plugins/dy_superpowers/mcp-server/tests/test_trace_integration.py`

预期：PASS；Codex 父子链路共用 trace ID，独立执行仍产生根 span，遥测关闭仍写本地 JSONL。

- [ ] **Step 6: 提交父上下文实现**

```bash
git add plugins/dy_superpowers/mcp-server/src/dy_superpowers_mcp/telemetry.py \
  plugins/dy_superpowers/mcp-server/tests/test_trace_parent_context.py \
  plugins/dy_superpowers/mcp-server/tests/test_trace_integration.py
git commit -m "feat: continue Codex MCP trace context"
```

### Task 4: 接通低级 JSON-RPC 和 Python MCP SDK 两条入口

**Files:**
- Modify: `plugins/dy_superpowers/mcp-server/src/server.py`
- Modify: `plugins/dy_superpowers/mcp-server/tests/test_stdio_protocol.py`
- Modify: `plugins/dy_superpowers/mcp-server/tests/test_trace_integration.py`

**Interfaces:**
- `execute_tool(name, arguments, workspace, *, parent_trace_context=None) -> tuple[object, dict | None]`。
- `handle(request, workspace)` 从 `params.meta` 提取 `_meta` 并传入 `execute_tool`。
- `run_mcp.call_tool(context, params)` 从 SDK 的 `params.meta` 读取同样的字段；若 SDK 版本没有 `meta` 属性按空上下文处理。

- [ ] **Step 1: 写两条入口的父子 Trace 测试**

使用低级 `handle` 构造 `params.meta.traceparent`，并对 SDK 回调使用 `SimpleNamespace(name=..., arguments=..., meta=...)`；两者均断言工具 span 与模拟 Codex span 同一 trace ID，错误响应仍保留 `-32602` 或 `-32603`。

- [ ] **Step 2: 运行入口测试，确认当前未传递 meta**

运行：`PYTHONPATH=plugins/dy_superpowers/mcp-server/src python3 -m pytest -q plugins/dy_superpowers/mcp-server/tests/test_stdio_protocol.py plugins/dy_superpowers/mcp-server/tests/test_trace_integration.py`

预期：父子链路测试失败，现有工具调用不读取 `params.meta`。

- [ ] **Step 3: 实现最小传递改动**

增加 `_parent_meta(params)` 辅助函数，只复制 `traceparent`、`tracestate` 两个键；将结果作为关键字参数传到 `execute_tool`。不得把整个 `_meta`、参数正文或认证数据写入 Trace。低级 JSON-RPC 和 SDK 共享同一个 `execute_tool` 生命周期，不复制 dispatch 表或业务处理器。

- [ ] **Step 4: 保持错误码语义**

低级路径继续返回 JSON-RPC `error.code`；SDK 路径把 `ToolRequestError` 映射为 MCP 错误时保留原始代码和消息，未知异常使用 `-32603`。父上下文解析失败不改变工具结果。

- [ ] **Step 5: 运行完整 MCP 测试**

运行：`PYTHONPATH=plugins/dy_superpowers/mcp-server/src python3 -m pytest -q plugins/dy_superpowers/mcp-server/tests`

预期：全部通过；本地 JSONL、工作区分组、敏感字段脱敏、OTLP 不可达降级和 Odoo 只读用例不回归。

- [ ] **Step 6: 提交入口改动**

```bash
git add plugins/dy_superpowers/mcp-server/src/server.py \
  plugins/dy_superpowers/mcp-server/tests/test_stdio_protocol.py \
  plugins/dy_superpowers/mcp-server/tests/test_trace_integration.py
git commit -m "fix: propagate parent trace through both MCP transports"
```

### Task 5: 完善 Basic Memory 的项目/模块索引

**Files:**
- Modify: `plugins/dy_superpowers/mcp-server/src/dy_superpowers_mcp/basic_memory.py`
- Modify: `plugins/dy_superpowers/mcp-server/tests/test_basic_memory_adapter.py`
- Modify: `plugins/dy_superpowers/docs/memory.md`

**Interfaces:**
- `BasicMemoryAdapter.search(query: str, *, module: str | None = None) -> dict`：默认只搜索当前工作区；提供模块时只搜索该模块目录。
- 返回结果中的每项包含 `module`、相对路径和摘要；不返回其他工作区路径。

- [ ] **Step 1: 写模块过滤和跨项目隔离测试**

创建两个模块和两个工作区的 Markdown 文件，使用真实或模拟 Basic Memory CLI，断言 `module="expense"` 不返回总账文件，也不返回另一个工作区内容；Basic Memory 不可用时本地回退结果同样遵守过滤。

- [ ] **Step 2: 运行测试，确认当前索引未定义模块边界**

运行：`PYTHONPATH=plugins/dy_superpowers/mcp-server/src python3 -m pytest -q plugins/dy_superpowers/mcp-server/tests/test_basic_memory_adapter.py`

预期：模块过滤断言失败或接口不存在。

- [ ] **Step 3: 实现模块路径白名单和搜索参数**

把模块目录加入索引输入白名单；检查每个路径的 `resolve()` 和符号链接，拒绝离开当前工作区。Basic Memory 项目名和配置目录继续按工作区隔离，不能改为共享全局项目。搜索返回 `module` 字段，回退搜索复用同一白名单。

- [ ] **Step 4: 记录搜索 Trace 关联**

调用 `dy_memory_search` 时从参数可选的 `trace_context.module` 获取模块过滤；没有模块时搜索全部当前项目模块，但事件仍记录命中的模块数量，不把其他项目路径或正文复制到属性字段。

- [ ] **Step 5: 运行 Basic Memory 和回退测试**

运行：`PYTHONPATH=plugins/dy_superpowers/mcp-server/src python3 -m pytest -q plugins/dy_superpowers/mcp-server/tests/test_basic_memory_adapter.py plugins/dy_superpowers/mcp-server/tests/test_research_and_memory_edges.py`

预期：Basic Memory 0.23.2 可用时命中模块结果；命令缺失、超时或非法 JSON 时返回 `available=false` 并完成本地搜索。

- [ ] **Step 6: 提交索引改动**

```bash
git add plugins/dy_superpowers/mcp-server/src/dy_superpowers_mcp/basic_memory.py \
  plugins/dy_superpowers/mcp-server/tests/test_basic_memory_adapter.py \
  plugins/dy_superpowers/docs/memory.md
git commit -m "feat: scope Basic Memory search to project modules"
```

### Task 6: 扩展 Trace 业务字段和本地保留策略验证

**Files:**
- Modify: `plugins/dy_superpowers/mcp-server/src/dy_superpowers_mcp/trace.py`
- Modify: `plugins/dy_superpowers/mcp-server/src/dy_superpowers_mcp/telemetry.py`
- Modify: `plugins/dy_superpowers/mcp-server/tests/test_trace_integration.py`
- Modify: `telemetry/README.md`

**Interfaces:**
- Trace 事件统一带 `project_id/workspace_id`、`module`、`check_key`、`scope_id`、`revision`、`workflow_id`；正文只放事件，不提升为 span 索引属性。
- 本地 JSONL 继续按工作区哈希、Trace ID 和日期滚动，并由 `max_bytes/max_age_days` 清理；OTLP 失败只输出简短 stderr。

- [ ] **Step 1: 写业务字段和保留策略测试**

断言调研读取、确认写入、补充调研和 Odoo 查询都能按模块/检查键关联；写入超过容量或超过保留日的记录后调用 `TraceStore.read`，断言旧文件被清理而当前工具调用仍返回成功。

- [ ] **Step 2: 运行现有 Trace 测试，定位字段缺口**

运行：`PYTHONPATH=plugins/dy_superpowers/mcp-server/src python3 -m pytest -q plugins/dy_superpowers/mcp-server/tests/test_trace_integration.py`

预期：新增模块筛选或保留策略断言先失败；既有脱敏和 OTLP 降级断言保持通过。

- [ ] **Step 3: 补充字段白名单和模块标签**

仅把稳定元数据提升到 OTel 属性；调研正文、记忆正文、搜索词和脱敏 domain 仍作为受长度限制的事件内容。`module` 来源必须是已校验的工具参数或清单键，不从路径字符串推断。

- [ ] **Step 4: 验证父子 Trace 与本地 Trace 对齐**

本地事件中的 `otel_trace_id`、`otel_span_id` 必须能定位到导出的插件 span；存在 Codex 父 span 时本地事件仍指向插件工具 span，不伪造 Codex span ID。

- [ ] **Step 5: 运行测试并提交**

运行：`PYTHONPATH=plugins/dy_superpowers/mcp-server/src python3 -m pytest -q plugins/dy_superpowers/mcp-server/tests/test_trace_integration.py plugins/dy_superpowers/mcp-server/tests/test_openobserve_e2e.py`

预期：本地和远程链路字段一致；远程服务不可达时本地记录、记忆、调研和 Odoo 查询仍成功。

```bash
git add plugins/dy_superpowers/mcp-server/src/dy_superpowers_mcp/trace.py \
  plugins/dy_superpowers/mcp-server/src/dy_superpowers_mcp/telemetry.py \
  plugins/dy_superpowers/mcp-server/tests/test_trace_integration.py \
  telemetry/README.md
git commit -m "test: verify module trace grouping and retention"
```

### Task 7: 增加固定结构巡检报告工具

**Files:**
- Create: `plugins/dy_superpowers/mcp-server/src/dy_superpowers_mcp/reporting.py`
- Modify: `plugins/dy_superpowers/mcp-server/src/server.py`
- Create: `plugins/dy_superpowers/mcp-server/tests/test_inspection_report.py`
- Modify: `plugins/dy_superpowers/mcp-server/tests/test_inspection_validation.py`
- Modify: `plugins/dy_superpowers/docs/delivery-domain.md`
- Modify: `plugins/dy_superpowers/docs/inspection-validation.md`

**Interfaces:**
- `inspection_report_path(workspace: Path, module: str, report_id: str) -> Path`：结果固定为 `<workspace>/.dy_superpowers/reports/inspection/<module>/<report_id>.md`；`module` 和 `report_id` 只能使用已校验标识。
- `render_inspection_report(workspace: Path, report: dict, validation: dict, trace_id: str) -> str`：输出固定中文 Markdown 章节和表格，不输出 Odoo 返回行、密码、令牌或未脱敏个人字段。
- `dy_inspection_report_write(trace_id, report, confirmed) -> {written, report_id, report_path, validation}`：工具内部重新执行 `validate_inspection`；`valid=false` 或 `confirmed` 不是 `true` 时不写文件。`gate=待确认` 可以形成报告，但必须在报告标题和摘要中保留待确认状态。

- [ ] **Step 1: 写报告门禁和固定目录失败测试**

```python
def test_report_write_requires_validated_scope_and_confirmation(tmp_path):
    report = build_valid_report(tmp_path)

    value, error = server.execute_tool("dy_inspection_report_write", {
        "trace_id": "inspection-report", "report": report, "confirmed": False,
    }, tmp_path)
    assert value is None
    assert error["code"] == -32602
    assert not (tmp_path / ".dy_superpowers/reports/inspection").exists()

    value, error = server.execute_tool("dy_inspection_report_write", {
        "trace_id": "inspection-report", "report": report, "confirmed": True,
    }, tmp_path)
    assert error is None
    path = Path(value["report_path"])
    assert path == tmp_path / ".dy_superpowers/reports/inspection/expense/inspection-report.md"
    assert path.read_text(encoding="utf-8").startswith("# 费用模块巡检报告")
```

- [ ] **Step 2: 提取可复用的有效报告构造器并写无效范围测试**

将现有 `test_inspection_validation.py` 的 `report` fixture 核心整理为 `build_valid_report(workspace: Path) -> dict`，供校验和报告测试共同使用。构造缺少 `expected_checks`、调研版本不匹配或验收标准不匹配的报告，断言工具返回 `-32602` 且目录没有文件。把密码、令牌和 Odoo 业务返回行放入输入，断言渲染结果被拒绝或经过现有脱敏器处理后不包含原文。

- [ ] **Step 3: 运行失败测试**

运行：`PYTHONPATH=plugins/dy_superpowers/mcp-server/src python3 -m pytest -q plugins/dy_superpowers/mcp-server/tests/test_inspection_report.py`

预期：失败，因为当前没有报告渲染器和 `dy_inspection_report_write` 工具。

- [ ] **Step 4: 实现固定报告路径和原子写入**

在 `reporting.py` 校验模块和报告标识，创建 `.dy_superpowers/reports/inspection/<module>/`，使用现有 `atomic_write` 和文件锁写入。禁止调用方传入绝对路径、目录穿越、任意扩展名或覆盖已有报告；同一 `report_id` 已存在时返回冲突错误，新的复查必须使用新的报告标识并通过 `previous_report_id` 建立引用。

- [ ] **Step 5: 实现固定中文报告模板**

报告固定包含：项目工作区标识、模块、报告编号、生成时间、Trace/Workflow 标识、矩阵版本、调研版本摘要、门禁摘要、按检查顺序和子项编号的结果表、证据引用、阻塞与待确认项、限制和复查引用。`通过` 只能来自校验后的记录；`待确认/未知/未检查` 原样保留，不能自动改成不通过或通过。

- [ ] **Step 6: 注册 MCP 工具并写 Trace**

在 `server.py` 注册 `dy_inspection_report_write`，调用前重新校验报告，调用后写入 `inspection.report.write` 事件，记录 `module`、报告编号、路径相对标识、`gate`、`valid` 和记录数；不记录报告正文到 span 属性。工具响应返回工作区相对路径和校验结果，避免暴露绝对路径。

- [ ] **Step 7: 运行报告工具测试**

运行：`PYTHONPATH=plugins/dy_superpowers/mcp-server/src python3 -m pytest -q plugins/dy_superpowers/mcp-server/tests/test_inspection_report.py plugins/dy_superpowers/mcp-server/tests/test_inspection_validation.py`

预期：PASS；有效报告写入固定目录，未确认、无效、路径冲突和敏感内容均被拒绝，报告 Trace 只保留可筛选元数据。

- [ ] **Step 8: 提交报告工具变更**

```bash
git add plugins/dy_superpowers/mcp-server/src/dy_superpowers_mcp/reporting.py \
  plugins/dy_superpowers/mcp-server/src/server.py \
  plugins/dy_superpowers/mcp-server/tests/test_inspection_report.py \
  plugins/dy_superpowers/mcp-server/tests/test_inspection_validation.py \
  plugins/dy_superpowers/docs/delivery-domain.md \
  plugins/dy_superpowers/docs/inspection-validation.md
git commit -m "feat: write validated inspection reports to workspace"
```

### Task 8: 更新架构文档和运行查询

**Files:**
- Modify: `plugins/dy_superpowers/docs/architecture.md`
- Modify: `plugins/dy_superpowers/docs/memory.md`
- Modify: `plugins/dy_superpowers/docs/runtime-boundaries.md`
- Modify: `telemetry/README.md`

**Interfaces:**
- 文档必须给出最终目录、MCP `_meta` 父上下文、独立运行回退、清单驱动多轮调研、模块级搜索和 Trace 查询字段。

- [ ] **Step 1: 更新目录和生命周期图**

加入以下事实目录示例，并说明模块记忆只能通过 MCP 工具查看：

```text
<project-workspace>/.dy_superpowers/
├── decisions.md
├── delivery-register.md
├── reports/
│   └── inspection/<module>/<report_id>.md
└── modules/
    ├── expense/research.md
    └── general-ledger/research.md
```

- [ ] **Step 2: 更新 Trace 链路说明**

明确正常链路为 `Codex mcp.tools.call → dy_superpowers 工具 span → operation span`；独立运行没有 Codex 父 span 时插件工具 span 为根。说明 `_meta` 只接收 `traceparent/tracestate`，业务 `trace_context` 只是筛选标签。

- [ ] **Step 3: 更新多轮调研说明**

说明初始化只返回清单状态和确认键；模块记忆只能通过 `dy_module_memory_read` 查看；每轮问题来自模块矩阵；确认后按模块文件写入；跨会话待办需要用户确认；巡检只消费 `confirmed` 记录。

- [ ] **Step 4: 更新巡检报告说明**

说明 `dy_inspection_validate` 是只读门禁，`dy_inspection_report_write` 是唯一报告落盘入口；报告固定写入 `.dy_superpowers/reports/inspection/<module>/`，报告编号不可覆盖，`待确认` 门禁可以保存但必须原样展示。

- [ ] **Step 5: 添加中文 OpenObserve 查询**

提供按 `dy_workspace_id`、`dy_module`、`dy_check_key`、`dy_workflow_id` 筛选的 SQL，并区分工具错误、记忆后端降级、巡检 `valid=false` 和调研 `待确认`。

- [ ] **Step 6: 检查文档与实现一致**

运行：`rg -n "module-research\\.md|独立根|强制根" plugins/dy_superpowers/docs plugins/dy_superpowers/skills telemetry/README.md`

预期：文档只描述新的模块目录和工具读取方式；不存在旧扁平文件兼容、隐式文件读取或强制根链路说明。

- [ ] **Step 7: 提交文档改动**

```bash
git add plugins/dy_superpowers/docs/architecture.md \
  plugins/dy_superpowers/docs/memory.md \
  plugins/dy_superpowers/docs/runtime-boundaries.md \
  telemetry/README.md
git commit -m "docs: describe project module memory and trace propagation"
```

### Task 9: 完成插件级回归和交付门禁

**Files:**
- Verify: `plugins/dy_superpowers/.codex-plugin/plugin.json`
- Verify: `plugins/dy_superpowers/.mcp.json`
- Verify: `plugins/dy_superpowers/mcp-server/pyproject.toml`
- Verify: `plugins/dy_superpowers/mcp-server/uv.lock`
- Verify: all tests under `plugins/dy_superpowers/mcp-server/tests` and repository `tests`

**Interfaces:**
- 不改变插件市场入口、MCP 名称、Basic Memory `0.23.2` 固定版本和工作区参数；所有新增能力通过既有插件入口可用。

- [ ] **Step 1: 安装锁定依赖并检查 Basic Memory 版本**

运行：`uv sync --frozen --project plugins/dy_superpowers/mcp-server && plugins/dy_superpowers/mcp-server/.venv/bin/basic-memory --version`

预期：依赖安装成功，版本输出为 `0.23.2`。

- [ ] **Step 2: 运行 MCP 全量 pytest**

运行：`PYTHONPATH=plugins/dy_superpowers/mcp-server/src uv run --frozen --project plugins/dy_superpowers/mcp-server pytest -q plugins/dy_superpowers/mcp-server/tests`

预期：全部通过；没有使用 `unittest`，父子 Trace、模块隔离、初始化清单、Odoo 只读和遥测降级均有证据。

- [ ] **Step 3: 运行插件市场布局和格式验证**

运行：`python3 -m pytest -q tests` 以及 `bash scripts/validate-plugin.sh`。

预期：市场布局、MCP 注册、Codex CLI 隔离安装和协议冒烟全部通过；若环境缺少官方 CLI 或 uv，必须把缺失作为验证失败原因写入结果，不能伪报成功。

- [ ] **Step 4: 运行差异和敏感信息检查**

运行：`git diff --check`；再搜索 `password|token|secret` 及内部测试账号、内网 IP 等敏感标识，确认只出现在明确的测试夹具或忽略文件，不进入插件清单、文档、Trace 样例和提交内容。

- [ ] **Step 5: 做一次真实独立运行和一次 Codex 父上下文运行**

独立运行验证：调用 `dy_session_initialize`、`dy_module_memory_read`、`dy_memory_search`、`dy_inspection_validate` 和 Odoo 只读工具，关闭或阻断 OTLP 后确认本地 JSONL 和业务结果仍存在。

父上下文运行验证：通过 Codex MCP 调用同一工具，在 OpenObserve 或内存导出器中确认 `mcp.tools.call → dy_superpowers` 的 `trace_id` 和 `parent_span_id` 关系。

- [ ] **Step 6: 形成交付记录**

在最终报告中列出：变更文件、测试命令和结果、模块记忆目录、报告文件路径、父子 Trace 证据、OTLP 降级证据、已知限制和回滚方法。未通过的门禁必须标明原因和阻塞项。

## 方案取舍与限制

1. **保留插件 Trace，但不再制造假根节点。** Codex 原生 Trace 只覆盖 MCP 调用边界，无法看到哪个模块记忆、哪个调研结论或哪个 Odoo domain 被使用；插件业务 span 仍需要保留。Codex 调用时复用父上下文，独立运行时再自建根节点。
2. **模块文件作为唯一调研事实源。** 每个模块拥有独立事实源和历史，减少跨模块误读；所有读取经过 `dy_module_memory_read`，文件路径不暴露给 skill。
3. **初始化返回索引，不返回答案正文。** 清单状态和确认键足以恢复任务，正文按检查键读取；这样不会把多模块、多轮内容一次性塞入 Codex 上下文，也避免把未确认候选答案误当事实。
4. **Basic Memory 是索引，不是事实源。** Markdown 模块文件负责确认、版本和审计；Basic Memory 只加速当前项目或当前模块搜索，索引故障回退本地搜索。
5. **远程遥测是旁路。** OTLP、OpenObserve 或 Collector 不可达时只影响远程可视化，本地 JSONL、模块记忆、调研确认和 Odoo 只读操作继续执行。
