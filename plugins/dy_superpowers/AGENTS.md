# dy_superpowers 插件约定

- 插件清单：`.codex-plugin/plugin.json`。
- MCP 注册：`.mcp.json`；服务入口：`mcp-server/src/server.py`。
- 技能目录：`skills/`；每个 skill 必须有中文 `SKILL.md`，并明确触发条件、输入、输出、记忆和证据边界。
- 工作区记忆只能写入当前项目的 `.dy_superpowers/`，不能写入插件包参考目录。
- 模块调研使用 `dy_research_answers_save` 批次写入、`dy_module_memory_read` 读取；按模块、模板问题和稳定 `scope_id` 保存；修订必须传入当前 `expected_revision`；只有确认且完整的答案可作为巡检与配置依据。
- 巡检报告须完整覆盖当前模板的全部问题；形成报告前使用 `dy_inspection_validate` 校验覆盖、调研版本和验收依据，再用 `dy_inspection_report_save` 经确认保存到工作区固定报告目录。
- Odoo 证据引用须能关联同一工作区 trace 中成功的同模型读取事件；证据引用存在不等于证据内容真实，门禁不授予业务写入权限。
- 巡检以当前确认答卷为业务依据；答卷缺口在报告中标待确认或未检查，不得猜测通过。
- Odoo 12 连接默认只读；写操作只允许仓库根 `AGENTS.md` 安全边界列出的受开关控制的白名单工具，不引入 `unlink`、通用 `execute_kw`、SQL 或 Docker 写操作。
- trace 使用工作区哈希标识；按项目要求记录记忆正文与 Odoo 查询 domain，密码/token 及常见个人字段值须脱敏，不记录 Odoo 返回行。
- 新增测试使用 pytest，不使用 unittest。现有 `mcp-server/tests/test_runtime.py` 为迁移前的既有测试，不作为新测试模板。
- 修改插件 manifest 后，从仓库根目录运行 `bash scripts/validate-plugin.sh`，检查配置与路径引用、hook 契约、MCP 协议冒烟，并通过官方 Codex CLI 在临时 `CODEX_HOME` 中注册市场、安装插件、确认启用状态。当前公开的官方 plugin-creator 不包含 `validate_plugin.py`，不要依赖该路径。
