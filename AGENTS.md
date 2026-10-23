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

- 使用中文编写新增文档、skill 和用户可见文本。
- 新功能先写 pytest 测试；不要新增 `unittest` 测试。`plugins/dy_superpowers/mcp-server/tests/test_runtime.py` 是迁移前的既有测试，除非专门安排测试迁移，不要复制其风格到新测试。
- MCP 测试：`PYTHONPATH=... python3 -m pytest -q plugins/dy_superpowers/mcp-server/tests`。
- 市场布局测试：`python3 -m pytest -q tests`。
- 完成前运行 `git diff --check` 和 `bash scripts/validate-plugin.sh`。缺少 Codex CLI 或 uv 时必须明确失败，不得把跳过安装测试视为校验通过。

## 安全边界

- Odoo 工具默认只读，不新增任意 RPC、SQL 或写操作。
- 记忆只属于当前工作区 `.dy_superpowers/`；调研写入必须有用户确认。
- trace 按工作区稳定标识分组，不得包含密码或令牌；按项目要求记录记忆正文和 Odoo 查询 domain，敏感值必须脱敏，Odoo 返回行不得记录。
