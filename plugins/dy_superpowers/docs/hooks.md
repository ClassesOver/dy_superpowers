# Codex Hook 使用边界

## 会话初始化

插件通过 `.codex-plugin/plugin.json` 声明 `hooks/hooks.json`，在 Codex `SessionStart` 调用同一插件内 MCP 服务的 `dy_session_initialize`。Codex 提供的会话 ID 用作 trace ID，来源字段用于区分启动、恢复、清空、压缩和 fork。

初始化工具只返回当前工作区记忆索引和读取指引，不读取或注入记忆正文，也不创建 `.dy_superpowers/`。需要项目背景、偏好、决策或交付台账时，按需调用 `dy_memory_read(trace_id, name)` 读取完整原文；模块巡检按检查键调用 `dy_research_record_read`，避免将整份调研记忆无差别加入上下文。MCP 记忆读取没有插件设置的字符截断上限。

注入内容是项目背景，不构成记忆写入、Odoo 操作或其他业务动作的授权。Codex hook 信任策略仍适用；启用 hook 后，MCP 服务进程会在会话初始化时启动。

## MCP 工具生命周期

MCP 工具调用经过统一运行时：

```text
参数校验
  -> tool.start
  -> 工具执行
  -> 业务事件 / memory 事件
  -> tool.error（失败时）
  -> tool.end
```

工具校验 trace ID、参数、记忆文件白名单及记忆写入确认状态。工作区记忆读取和 SessionStart 仅追加操作元数据到 trace，包含操作类型、来源和文件名，不记录记忆正文。模块调研问答、员工信息和业务明细也不写入 trace。

外部 OpenTelemetry 不可达时，插件继续使用本地 JSONL trace；遥测后端可用性不是 MCP 工具成功的前置条件。

## 业务边界

- `module-research` 在写入调研结论前，展示整理结果并取得用户确认。
- `expense-inspection` 和 `general-ledger-inspection` 在检查子项前，按检查键读取对应调研结论，并结合“验收标准（调研后）”判定。
- 调研缺失、含糊或冲突时，暂停依赖该结论的子项，进行多轮补充问答；确认后再回到原检查项。
- 只有确认后的调研结论才能驱动巡检。
- Odoo 工具保持只读；hook 不用于扩大业务操作权限。
