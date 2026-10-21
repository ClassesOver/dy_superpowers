# dy_superpowers 插件约定

- 插件清单：`.codex-plugin/plugin.json`。
- MCP 注册：`.mcp.json`；服务入口：`mcp-server/src/server.py`。
- 技能目录：`skills/`；每个 skill 必须有中文 `SKILL.md`，并明确触发条件、输入、输出、记忆和证据边界。
- 工作区记忆只能写入当前项目的 `.dy_superpowers/`，不能写入插件包参考目录。
- 模块调研使用 `dy_research_record_write/read`，按模块、检查顺序、子项编号和稳定 `scope_id` 保存；修订必须传入当前 `expected_revision`，停用须明确确认与原因；只有 `confirmed` 结论可驱动巡检。
- 巡检形成报告前须用 `dy_inspection_validate` 校验声明范围的覆盖、调研版本和验收依据；校验不验证证据真实性，不授予保存或业务写入权限。
- 巡检必须结合调研结论和“验收标准（调研后）”，缺口进入多轮补充调研，不得猜测通过。
- Odoo 12 连接默认只读；不要引入 `create`、`write`、`unlink`、通用 `execute_kw`、SQL 或 Docker 写操作。
- 新增测试使用 pytest，不使用 unittest。现有 `mcp-server/tests/test_runtime.py` 为历史兼容测试，不作为新测试模板。
- 修改插件 manifest 后，用 Codex 官方 plugin-creator 技能自带的 `validate_plugin.py` 对插件根目录执行校验（脚本位于本机 Codex 安装的 plugin-creator 技能示例目录，路径以本机为准）。
