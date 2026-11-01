# 工作区记忆

`dy_module_memory_read(trace_id, module, scope_id?, question_ids?)` 读取当前模板问题、最新确认答案及附件明细；只接受这些参数，不接受文件路径或旧检查键。读取会校验答卷版本完整性，不因筛选忽略损坏。

`dy_inspection_prepare(trace_id, module, scope_id, environment, checks?, previous_records?)` 从当前模板和同范围确认答卷生成只读准备视图；不会创建第二份事实源或写入待办。`verification.performed=false` 表示未执行本轮巡检。只认显式 `research_complete=true`；准备就绪仍需核对验收条件、环境和实时证据，不能当作通过。

新准备接口的 `environment` 使用与后续证据 `trace_context.environment` 一致的稳定标识（至多128字符，例如 `UAT`、`PROD`），仅允许字母、数字、下划线、点、冒号、斜线、井号和连字符；中文名称或带空格的显示名应先由用户明确其对应标识，不能自行翻译猜测或与其他环境合并。

`previous_records` 只做格式校验，不用于复用结论：每个问题都返回 `recheck_required=true`、`evidence_reusable=false`，须按正式巡检校验流程重新取证。范围或期间变化应使用经确认的范围标识或调研修订；工具不能从自由文本自动推断业务变化。

记忆范围固定为当前工作区，且一个工作区只对应一个项目。

记忆以工作区 `.dy_superpowers/` 下的 Markdown 文件为事实源。MCP 记忆工具只允许读取或写入下列文件；写入前必须由用户明确确认。不存在的文件读取为空内容。trace 记录读取/写入正文、搜索词与搜索摘要，并附文件名和字节数；单条正文最多 64 KiB，超出时标记 `content_truncated`。凭据及常见个人字段值会脱敏。

Codex `SessionStart` 在启动、恢复、清空、压缩和 fork 时调用 `dy_session_initialize`。它只返回文件索引和按需读取指引，不读取或注入记忆正文，因此不会因插件设置的字符上限截断记忆；初始化不创建记忆目录或文件。需要某份项目记忆时，通过 `dy_memory_read` 原样读取完整文件。模块调研答卷通过 `dy_module_memory_read` 按范围和问题读取。

- `preferences.md`：当前工作区内的工作偏好。
- `profile.md`：该项目的稳定背景、版本和长期约束。
- `decisions.md`：该项目的已确认决策。
- `delivery-register.md`：插件维护的交付台账。
- `skill-candidates.md`：待人工审阅的重复工作模式。
- `modules/<module>/qa/`：经用户确认的模块调研答卷，按范围 `scope_id` 分目录保存不可变版本（答案、附件明细与同步答卷）。只允许由 `dy_research_answers_save` 批次写入，通用 `dy_memory_write` 不可覆盖。
- `reports/<module>/<YYYY-MM-DD>/<report-id>.md`：经模板覆盖与证据引用校验、用户确认后保存的巡检报告；报告编号由保存工具生成，用 `dy_inspection_report_read` 按模块和响应中的报告编号读取。

答卷修订按问题校验：每题提交 `expected_revision`（新题为 `null`），与当前确认修订不一致时拒绝，需重新读取、核对差异并确认。同批同内容幂等；内容变化使用新批次号，并生成新的不可变版本。

不得跨工作区读取、复制或合并这些文件。密码、令牌、员工个人信息和生产业务明细不得写入记忆。记忆正文会进入本地 JSONL trace；插件不使用外部遥测，trace 不会发送到任何远端服务。记录中匹配到凭据和常见个人字段时会脱敏，但这不是完整的数据分类器。

Basic Memory `0.23.2` 是插件必需的本地 Markdown 索引后端，运行环境要求 Python 3.12 及以上。它由现有 `dy-superpowers-odoo` MCP 进程按需调用 CLI，不注册第二个 MCP Server，也不改变 Codex 的插件入口。每个工作区使用项目名 `dy-superpowers`，项目路径固定为当前工作区 `.dy_superpowers/`；Basic Memory 配置和数据库放在工作区隐藏兄弟目录 `.dy_superpowers-basic-memory/`，避免被自身索引。可用时 `dy_memory_search` 返回 Basic Memory 结果；命令缺失、超时、返回非法 JSON 或索引失败时回退本地 Markdown 关键词搜索，插件功能不被运行故障阻断，并以 `available=false` 明确标识降级；降级不是可省略依赖的安装方式。

Basic Memory 已列入插件运行时依赖并由 `uv.lock` 固定版本，正常启动时自动安装到插件虚拟环境，无需 `uv tool install`。提前准备依赖可在市场仓库根目录执行：

```bash
uv sync --frozen --project plugins/dy_superpowers/mcp-server
```

默认调用插件当前 Python 环境中的 `basic-memory` CLI，避免误用全局版本；可通过 `DY_BASIC_MEMORY_COMMAND` 指定运维管理的同版本 CLI 路径。`BASIC_MEMORY_CONFIG_DIR`、`BASIC_MEMORY_MCP_PROJECT` 由适配器按工作区设置，不使用共享默认项目。Basic Memory 只用于搜索加速，不能覆盖 Markdown 事实源；trace 会记录搜索词及返回摘要。

适配器在每次搜索前运行 `reindex --search --project dy-superpowers` 刷新全文索引，并固定使用文本搜索、关闭语义模型加载。该方式不需要下载向量模型；刷新失败或超时则回退本地搜索。搜索前检查记忆目录、索引目录及其子项的符号链接，拒绝通过链接访问其他路径；回退读取复用工作区记忆白名单和路径校验。

索引进程固定设置 Basic Memory 官方选项 `BASIC_MEMORY_ENSURE_FRONTMATTER_ON_SYNC=false`、`BASIC_MEMORY_DISABLE_PERMALINKS=true`，禁止搜索期间向事实源添加 frontmatter 或改写 permalink。已有元数据和文件末尾保持原样；只读搜索不能触发未经确认的记忆修改或版本冲突。

跨会话调研待办复用 `delivery-register.md`：用户确认后保存问题、阻塞原因和下一条问题，不保存未确认答案。恢复时读取台账并复核最新确认答卷；待办本身不构成验收依据。

更新已有普通记忆时，先用 `dy_memory_read` 获取完整原文，再把原文作为 `dy_memory_write` 的 `expected_content` 传入；首次创建可省略。工具在进程锁内比较原文并原子替换，冲突时拒绝覆盖。发生冲突应重新读取、合并差异、确认后重试，不得直接使用最新原文作为令牌覆盖其他会话的新内容。

巡检报告保存在当前工作区 `.dy_superpowers/reports/<module>/<YYYY-MM-DD>/<report-id>.md`。保存工具会重新校验报告并要求明确确认；工具使用 UTC 时间戳与随机唯一后缀生成编号，模型无需猜编号，既有报告不覆盖。报告用 Markdown 包含门禁、模板与答卷版本和完整结构化巡检数据。项目待办仍记录在 `delivery-register.md`，记录要求见[交付领域](delivery-domain.md)。Basic Memory 仅是索引后端，不负责确认、修订或验收状态管理；配置结构损坏时回退当前 Markdown 搜索。

搜索返回 `has_more` 和完整性说明。本地回退同时提供总匹配行数及 `read_full_files`；每份文件最多展示 20 行，可通过 `dy_memory_read` 获取全文。Basic Memory 满页或后端标记存在更多结果时，视为可能未取全；搜索片段可能包含历史，调研答案必须通过 `dy_module_memory_read` 按当前版本复核。
