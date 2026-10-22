# 工作区记忆

记忆范围固定为当前工作区，且一个工作区只对应一个项目。

记忆以工作区 `.dy_superpowers/` 下的 Markdown 文件为事实源。MCP 记忆工具只允许读取或写入下列文件；写入前必须由用户明确确认。不存在的文件读取为空内容。插件追踪会记录记忆操作类型、结果和文件名，不记录正文。

Codex `SessionStart` 在启动、恢复、清空、压缩和 fork 时调用 `dy_session_initialize`。它只返回文件索引和按需读取指引，不读取或注入记忆正文，因此不会因插件设置的字符上限截断记忆；初始化不创建记忆目录或文件。需要某份项目记忆时，通过 `dy_memory_read` 原样读取完整文件。巡检调研记录应按检查键调用 `dy_research_record_read`，避免读取整份调研历史。

- `preferences.md`：当前工作区内的工作偏好。
- `profile.md`：该项目的稳定背景、版本和长期约束。
- `decisions.md`：该项目的已确认决策。
- `delivery-register.md`：插件维护的交付台账。
- `skill-candidates.md`：待人工审阅的重复工作模式。
- `module-research.md`：经用户确认的模块调研结论、依据、适用范围和状态。该文件只允许由 `dy_research_record_write` 按检查键追加修订，通用 `dy_memory_write` 不可覆盖。

模块调研通过 `dy_research_record_write` 按“模块 + 检查顺序 + 子项编号”保存；每次确认修订都保留历史版本，旧版本标记为 `superseded`。`dy_research_record_read` 按检查键返回最新版本。只有状态为 `confirmed` 的记录可驱动巡检。通用 `dy_memory_write` 仅允许更新其他项目记忆文件，避免整文件覆盖调研历史。追踪只记录键、操作和状态，不记录答案正文。

不得跨工作区读取、复制或合并这些文件。密码、令牌、员工个人信息和生产业务明细不得写入记忆。trace 仅保存记忆工具的操作状态和文件名，不记录记忆正文。

Basic Memory `0.23.2` 是可选的本地 Markdown 索引后端。它由现有 `dy-superpowers-odoo` MCP 进程按需调用 CLI，不注册第二个 MCP Server，也不改变 Codex 的插件入口。每个工作区使用项目名 `dy-superpowers`，项目路径固定为当前工作区 `.dy_superpowers/`；Basic Memory 配置和数据库放在工作区隐藏兄弟目录 `.dy_superpowers-basic-memory/`，避免被自身索引。索引仅在记忆 Markdown 的路径、大小或修改时间变化时重建，指纹保存在 `.dy_superpowers-basic-memory/index-state.json`。可用时 `dy_memory_search` 返回 Basic Memory 结果；命令缺失、超时、返回非法 JSON 或索引失败时回退本地 Markdown 关键词搜索，插件功能不被外部依赖阻断。

安装可选 CLI：

```bash
uv tool install --prerelease=allow basic-memory==0.23.2
```

可通过 `DY_BASIC_MEMORY_COMMAND` 指定 CLI 路径。`BASIC_MEMORY_CONFIG_DIR`、`BASIC_MEMORY_MCP_PROJECT` 由适配器按工作区设置，不使用共享默认项目。Basic Memory 只用于搜索加速，不能覆盖 Markdown 事实源，也不能把查询正文或记忆正文写入 trace。

适配器在每次搜索前运行 `reindex --search --project dy-superpowers` 刷新全文索引，并固定使用文本搜索、关闭语义模型加载。该方式不需要下载向量模型；刷新失败或超时则回退本地搜索。搜索前检查记忆目录、索引目录及其子项的符号链接，拒绝通过链接访问其他路径；回退读取复用工作区记忆白名单和路径校验。

跨会话调研待办复用 `delivery-register.md`：用户确认后保存检查键、阻塞原因和下一条问题，不保存未确认答案。恢复时读取台账并按检查键复核最新确认结论；待办本身不构成验收依据。
