# 总账源码与只读查询

技术映射的完整依据见 [HRP 源码索引](../../../references/hrp-source-index.md)。实际模块版本、字段及授权范围仍须通过当前账套核验；定制 addon 可能变更字段，以下 domain 只是形状参考。

| 业务能力 | 技术模块 | 关键配置模型 |
| --- | --- | --- |
| 财务会计基础 | `dy_fin_mdm` | `dy.fin.account.structure`、`dy.fin.account.period`、`dy.fin.account.analytic.plan`、`dy.fin.cash.flow.mapping`、`dy.account.permissions.settings` |
| 财务凭证管理 | `dy_fin_acc` | `account.move`、`dy.fin.acc.account.periodicity.voucher`（经常性凭证）、`dy.fin.acc.carry.forward.project.definition`（结转项目与顺序）、`dy.fin.acc.carry.forward.template`（结转模板）、`dy.fin.acc.account.move.discrete.number` |
| 预算会计 | `dy_fin_gaap` | `account.mapping`、`budget.account.move`；两个预算向导见下文 |
| 财务会计平台基础 | `dy_fin_pf` | `dy.fin.pf.account.class.define`、`dy.fin.pf.account.move.generation.rule`、`dy.fin.pf.influence.factor.setting` |

`dy_fin_pf` 是业务凭证生成规则共同入口，按最新确认答卷的本期适用范围核对，不能因问题未直接命名而推断必装或不适用。

- 期间与开关账：持久模型 `dy.fin.account.period` 按 `company_id/date_from/date_to/account_structure_id` 筛选，读取 `state/state_show/cashier_period_state`。`dy.fin.account.period.open.and.done` 是 `TransientModel` 操作向导，只有开关选项，没有公司或期间状态字段；仅可读元数据，不能查向导行判断开账，更不能执行向导。开关顺序及权限反例须有既有日志、授权操作证据或人工验证。
- 科目与辅助：查 `account.account`、`dy.fin.account.structure` / `dy.fin.account.structure.line`；公司适用结构由结构行 `dy.fin.account.structure.line.company_id` 关联其结构头 `head_id` 推导。辅助核算查 `dy.fin.account.analytic.plan` 及实例。辅助方案、现金流映射 `dy.fin.cash.flow.mapping` 和财务预算映射 `account.mapping` 的记录归属用 `company_id`；它们的 `company_ids` 是不可直接搜索的界面候选计算字段，不能作归属 domain。
- 分类账：查 `account.journal`，先限定确认的 `account_structure_id`，再取 `journal_company_ids` 为空或包含目标公司的记录；读取这两个字段及 `active`。只按可空 `company_id` 会漏共享分类账。真实范围字段缺失时停止该配方，不能退回通用公司过滤。
- 财务/预算映射与平行记账：查 `account.mapping`，核对方向、公司和适用范围；结果证据查 `budget.account.move` 与 `account.move` 的关联。现金流量表项 `dy.fin.account.cash.flow.statement.entry` 按 `account_structure_id` 限定结构，同时保留 `account_structure_id=False` 的共享预置记录。`budget.move.paralleled.wizard` 和 `budget.statement.of.income.and.expenditure` 为临时向导，仅可读字段元数据，不查向导行作配置或结果证据，不调用执行方法。
- 凭证状态与平衡：查 `account.move`，domain 如 `[["company_id","=",<公司>],["state","!=","draft"],["date",">=","<期间起>"],["date","<=","<期间止>"]]`；字段核实后取 `id/company_id/journal_id/date/state`，不取 `ref/narration` 等正文。借贷平衡按选定凭证查 `account.move.line` 的 `move_id/debit/credit`，逐页取全后按凭证勾稽。
- 期末结转：查 `dy.fin.acc.carry.forward.project.definition` 的 `carry_forward_order` 和 `dy.fin.acc.carry.forward.template` / `dy.fin.acc.carry.forward.template.line` 核验项目、模板与顺序；对应模型按已确认公司、科目结构、月末/年末类型及期间限定，字段先查元数据，不给不存在的公司/期间字段套通用过滤。执行头查 `dy.fin.acc.carry.forward.entry` 的 `template_id/line_ids`；执行行查 `dy.fin.acc.carry.forward.entry.line` 的 `head_id/carry_forward_period_id/carry_forward_project_id/carry_forward_order`，财务结转通过 `carry_forward_account_move_id` 回链生成的 `account.move`，预算结转通过 `carry_forward_budget_move_id` 回链生成的 `budget.account.move`。两类凭证分别核验，不以周期凭证模板替代结转结果；不创建记录或执行结转。
- 经常性凭证：`dy.fin.acc.account.periodicity.voucher` 用于经常性凭证模板，不能作期末结转证据。
