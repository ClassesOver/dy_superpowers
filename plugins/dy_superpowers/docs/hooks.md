# Codex Hook 使用边界

## 会话初始化

插件通过 `.codex-plugin/plugin.json` 声明 `hooks/hooks.json`，在 Codex `SessionStart` 调用同一插件内 MCP 服务的 `dy_session_initialize`。Codex 提供的会话 ID 用作 trace ID，来源字段用于区分启动、恢复、清空、压缩和 fork。

初始化工具只返回当前工作区记忆索引和读取指引，不读取或注入记忆正文，也不创建 `.dy_superpowers/`。需要项目背景、偏好、决策或交付台账时，按需调用 `dy_memory_read(trace_id, name)` 读取完整原文；模块巡检调用 `dy_module_memory_read`，按检查键读取时从 `records[0]` 获取记录，避免把整份调研历史加入上下文。MCP 记忆读取没有插件设置的字符截断上限。

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

工具校验 trace ID、参数、记忆文件白名单及记忆写入确认状态。每个本地事件都带稳定的 `workspace_id`；工具生命周期记录状态、耗时和错误类别。记忆读取/写入记录 Markdown 正文，搜索记录搜索词与返回摘要，调研读取/写入记录对应记录内容；Odoo 搜索记录 domain 条件和值，不记录返回行。密码、令牌及常见个人字段值脱敏，正文单条上限为 64 KiB。

插件不使用外部遥测；本地 JSONL trace 是唯一持久化追踪通道，不存在对外部遥测后端的可用性依赖。

## 业务边界

- `module-research` 在写入调研结论前，展示整理结果并取得用户确认。
- `expense-inspection` 和 `general-ledger-inspection` 在检查子项前，按检查键与稳定范围标识读取对应调研结论，并结合“验收标准（调研后）”判定。
- 调研缺失、含糊或冲突时，暂停依赖该结论的子项，进行多轮补充问答；确认后再回到原检查项。
- 只有当前 `confirmed` 调研结论才能驱动巡检；`retired`、损坏或过期版本均须阻断依赖项。
- 巡检报告在声明范围后经 `dy_inspection_validate` 检查；校验成功不构成写入授权或项目整体交付通过。
- Odoo 工具保持只读；hook 不用于扩大业务操作权限。

首次安装的依赖准备仍取决于 `uv` 和网络；初始化 hook 的 10 秒超时不代表冷启动已完成验收。索引未注入时可在会话内显式调用 `dy_session_initialize` 恢复读取指引，随后按需读取记忆；工具不会因此自动创建记忆。

本地 trace 写入失败时，业务工具保留实际执行结果，并向 stderr 输出不含正文的错误类型。显式 `dy_trace_event` 返回 `stored=false`，不能据此声称日志已保存；修复日志目录后再验证追踪能力。
