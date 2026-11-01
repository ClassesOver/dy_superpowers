# dy_superpowers 仓库约定

## 目录边界

- `plugins/dy_superpowers/` 是 Codex 插件包，包含 `.codex-plugin/plugin.json`、`.mcp.json`、skills、MCP 服务、参考资料和插件测试。
- `.agents/plugins/marketplace.json` 是本地市场 catalog，插件 source 必须指向 `./plugins/dy_superpowers`。
- 根仓库只保留一个 `.git`；不要在插件子目录创建嵌套 Git 仓库。

## 插件格式

- 修改 `.codex-plugin/plugin.json` 后运行 `bash scripts/validate-plugin.sh`；入口检查配置与路径引用、hook 契约、官方 Codex CLI 隔离市场安装和 MCP 协议冒烟。
- `plugin.json` 的 `name` 必须与插件目录和 marketplace 条目一致。
- MCP 必须通过 `plugin.json` 的 `mcpServers` 和插件内 `.mcp.json` 注册。
- 插件不使用外部遥测；本地 JSONL trace 是唯一持久化追踪通道。

## 开发与验证

- 每次执行 `git commit` 前，必须更新 `plugins/dy_superpowers/.codex-plugin/plugin.json` 的 `version`，并将版本号变更包含在同一次提交中；不得沿用上一次提交的版本号。
- 使用中文编写新增文档、skill 和用户可见文本。
- 新功能先写 pytest 测试；不要新增 `unittest` 测试。`plugins/dy_superpowers/mcp-server/tests/test_runtime.py` 是迁移前的既有测试，除非专门安排测试迁移，不要复制其风格到新测试。
- MCP 测试（在仓库根目录执行，使用插件锁定的 uv 环境）：`PYTHONPATH=plugins/dy_superpowers/mcp-server/src uv run --frozen --project plugins/dy_superpowers/mcp-server --extra test pytest -q plugins/dy_superpowers/mcp-server/tests`。
- 市场布局测试：`uv run --frozen --project plugins/dy_superpowers/mcp-server --extra test pytest -q tests`。
- 完成前运行 `git diff --check` 和 `bash scripts/validate-plugin.sh`。缺少 Codex CLI 或 uv 时必须明确失败，不得把跳过安装测试视为校验通过。

## 文档与 Skill 边界

- Skill 及其 `references/` 只保留运行所需的业务规约：问题、提取要素、验收关联、附件字段和记忆规则。
- 来源审计信息统一写入仓库本地 `docs/research-sources/`，包括源文件名、版本、工作表/单元格坐标、解析统计、映射缺口和转换说明；不得写入插件内的 Skill、参考表或插件目录约束文件。
- 业务字段中有实际含义的“来源”（例如凭证来源、数据来源）可以保留；仅用于证明资料出处的“来源定位”“验收定位”等审计字段不得保留在 Skill 参考表。
- 本地来源审计文档不属于 Skill 运行输入，不应被 Skill 自动加载或写入项目业务记忆。
- 本规则是仓库级文档边界；若插件内现有约定要求在 Skill 中保留来源审计内容，以本规则为准，不要为此新增或修改插件目录内的约束文件。

## 安全边界

- Odoo 工具默认只读。写能力仅限白名单配置创建/更新（`dy_odoo_record_create/update`）、配置导入（`dy_odoo_import_execute`）和模块安装/卸载（`dy_odoo_module_install/uninstall`）：对应写开关（`DY_ODOO_CONFIG_WRITE_ENABLED`、`DY_ODOO_MODULE_WRITE_ENABLED`）由宿主进程显式开启前，这些工具不出现也不可调用；执行前须预览并取得用户对精确确认摘要的确认，使用幂等键，执行后回读。不新增任意 RPC、SQL、记录删除、模块升级或业务单据动作；扩大写能力须同步修改本约定。
- 记忆只属于当前工作区 `.dy_superpowers/`；调研写入必须有用户确认。
- trace 按工作区稳定标识分组，不得包含密码或令牌；按项目要求记录记忆正文和 Odoo 查询 domain，敏感值必须脱敏，Odoo 返回行不得记录。
