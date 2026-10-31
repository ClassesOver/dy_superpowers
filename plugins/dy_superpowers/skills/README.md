# 插件技能

这里放置 Codex 技能。每个技能应负责对话触发、输入收集、审批和结果表达；需要确定性规划、验证或 Odoo 操作时调用插件内 MCP 工具。

当前已提供：

- `configuration`：通用配置检索、依赖分析、确认后受限 MCP 写入与双重验收；
- `module-research`：模块多轮调研与项目记忆；
- `expense-inspection`：费用模块交付巡检；
- `general-ledger-inspection`：总账模块交付巡检；
- `inspection-skill-creator`：创建或归一化领域巡检 skill，核对清单、源码配方与工具支持状态。

领域巡检共用 [执行流程](../references/inspection-workflow.md)，领域清单和源码配方保存在各自 `references/`。后续业务技能通过创建器生成；新增目录不自动获得正式巡检校验和报告能力。
