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

模块调研通过 `dy_research_record_write` 按“模块 + 检查顺序 + 子项编号 + 适用范围”保存；每次确认修订都保留历史版本，旧版本标记为 `superseded`。`dy_research_record_read` 按检查键和 `scope` 返回该范围的最新版本。同一检查键存在多个范围时，必须指定 `scope_id` 或 `scope`，不能默认采用最后写入的公司结论。旧记录兼容原有读取方式；不同范围各自维护历史；后续沿用返回的稳定 `scope_id`，显示文本可在携带当前版本时修订。首次未指定标识时按规范化范围文本生成，斜线两侧空格和全角字符的差异不会新建范围。只有状态为 `confirmed` 的记录可驱动巡检。通用 `dy_memory_write` 仅允许更新其他项目记忆文件，避免整文件覆盖调研历史。追踪只记录键、操作和状态，不记录答案正文。

不得跨工作区读取、复制或合并这些文件。密码、令牌、员工个人信息和生产业务明细不得写入记忆。trace 仅保存记忆工具的操作状态和文件名，不记录记忆正文。

Basic Memory `0.23.2` 是可选的本地 Markdown 索引后端。它由现有 `dy-superpowers-odoo` MCP 进程按需调用 CLI，不注册第二个 MCP Server，也不改变 Codex 的插件入口。每个工作区使用项目名 `dy-superpowers`，项目路径固定为当前工作区 `.dy_superpowers/`；Basic Memory 配置和数据库放在工作区隐藏兄弟目录 `.dy_superpowers-basic-memory/`，避免被自身索引。可用时 `dy_memory_search` 返回 Basic Memory 结果；命令缺失、超时、返回非法 JSON 或索引失败时回退本地 Markdown 关键词搜索，插件功能不被外部依赖阻断。

安装可选 CLI：

```bash
uv tool install --prerelease=allow basic-memory==0.23.2
```

可通过 `DY_BASIC_MEMORY_COMMAND` 指定 CLI 路径。`BASIC_MEMORY_CONFIG_DIR`、`BASIC_MEMORY_MCP_PROJECT` 由适配器按工作区设置，不使用共享默认项目。Basic Memory 只用于搜索加速，不能覆盖 Markdown 事实源，也不能把查询正文或记忆正文写入 trace。

适配器在每次搜索前运行 `reindex --search --project dy-superpowers` 刷新全文索引，并固定使用文本搜索、关闭语义模型加载。该方式不需要下载向量模型；刷新失败或超时则回退本地搜索。搜索前检查记忆目录、索引目录及其子项的符号链接，拒绝通过链接访问其他路径；回退读取复用工作区记忆白名单和路径校验。

跨会话调研待办复用 `delivery-register.md`：用户确认后保存检查键、阻塞原因和下一条问题，不保存未确认答案。恢复时读取台账并按检查键复核最新确认结论；待办本身不构成验收依据。

更新已有普通记忆时，先用 `dy_memory_read` 获取完整原文，再把原文作为 `dy_memory_write` 的 `expected_content` 传入；首次创建可省略。工具在进程锁内比较原文并原子替换，冲突时拒绝覆盖。发生冲突应重新读取、合并差异、确认后重试，不得直接使用最新原文作为令牌覆盖其他会话的新内容。

调研文件的可见 Markdown 与内部记录必须一致。工具读取和修订时会校验一致性；发现手动改动导致分歧时拒绝使用，不选择其中一份作为事实。异常仅阻断对应检查键和范围，不阻断其他可识别记录。先核对原确认依据并向用户展示拟修复内容，再用 `dy_research_record_write(repair=true, expected_revision=错误中的块版本, ...)` 提交经确认的修复；原损坏块保存在 `history`，标记 `invalidated`。无法定位的任意文件结构损坏仍需人工恢复备份，不应猜测其身份。该校验用于发现意外分歧，隐藏目录和一致性检查均不提供防篡改认证。

巡检结果与复查复用 `delivery-register.md`，记录要求见 [交付领域](delivery-domain.md)。Basic Memory 仅是索引后端，不负责确认、修订或验收状态管理；配置结构损坏时回退当前 Markdown 搜索。

调研修订必须传入 `expected_revision`，值取自最近一次 `dy_research_record_read` 返回的 `revision`，旧文件也支持此读取方式。版本根据原始记录块计算，不在读取时重写文件；有并发修订或手动编辑时拒绝过期版本。进程锁防止文件写入交错，版本检查防止过期结论成为当前事实。首次创建可省略版本；冲突后必须重新核对并确认。

范围停用按调研检查键执行：沿用当前 `scope_id`、`expected_revision`，经用户确认传入 `status=retired` 与 `retirement_reason`。历史保留，读取返回停用状态，巡检不能使用该记录。停用一条记录不自动停用同范围其他检查键；恢复或替代范围也要明确确认与修订。

搜索返回 `has_more` 和完整性说明。本地回退同时提供总匹配行数及 `read_full_files`；每份文件最多展示 20 行，可通过 `dy_memory_read` 获取全文。Basic Memory 满页或后端标记存在更多结果时，视为可能未取全；搜索片段可能包含历史，调研结论必须通过专用工具按当前版本复核。
