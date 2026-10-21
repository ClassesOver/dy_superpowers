# 插件技能

这里放置 Codex 技能。每个技能应负责对话触发、输入收集、审批和结果表达；需要确定性规划、验证或 Odoo 操作时调用插件内 MCP 工具。

当前已提供：

- `module-research`：模块多轮调研与项目记忆；
- `expense-inspection`：费用模块交付巡检；
- `general-ledger-inspection`：总账模块交付巡检。

后续业务技能继续按领域独立放置在 `skills/<name>/`，并将详细手册或规则放在对应的 `references/`。
