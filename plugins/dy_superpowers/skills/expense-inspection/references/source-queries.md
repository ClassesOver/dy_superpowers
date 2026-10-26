# 费用源码与只读查询

技术映射的完整依据见 [HRP 源码索引](../../../references/hrp-source-index.md)。实际模块版本、字段及授权范围仍须通过当前账套核验；定制 addon 可能变更字段，以下 domain 只是形状参考。

| 业务能力 | 技术模块 | 关键配置模型 |
| --- | --- | --- |
| 费用管理 | `dy_expense` | `dy.expense.document.type`、`dy.expense.item`、`dy.expense.city.season`、`dy.expense.permission.configuration` |
| 会计平台-费用 | `dy_fin_expense_pf` | `dy.fin.expense.pf`；生成规则复用 `dy_fin_pf` 的 `dy.fin.pf.account.move.generation.rule` |
| 预算会计平台-基础（按制度适用） | `dy_fin_budget_pf` | 扩展预算凭证通道；基础安装不替代费用预算集成及双分录样例核验 |
| 预算控制与费用集成 | `dy_budget_control_expense` | `dy.budget.control.rule.expense.item.line`、`dy.budget.control.exp.app.line`；主规则为 `dy_budget_control` 的 `dy.budget.control.execution.rule` |
| 专项经费与费用集成 | `dy_hrfm_expense`，依赖 `dy_hrfm`、`dy_expense` | 主规则为 `dy_hrfm` 的 `dy.hrfm.expense.execute.rule` |

- 单据类型与编号：`dy.expense.document.type` 的公司范围是多对多 `company_ids`，为空表示共享类型；启用字段为 `is_active`。核对字段后，在 `dy_odoo_record_search` 中使用 `args=[[["is_active","=",true],"|",["company_ids","in",[公司ID]],["company_ids","=",false]]]`，`kwargs={"fields":["id","name","company_ids","is_active","document_type"],"limit":100,"offset":0}`，替换实际公司 ID 并分页。这只是公司可用范围，实际岗位权限须单独核验；其他模型的空公司语义不能照搬。编号规则查 `dy.expense.document.type.sequence` 的 `company_id/doc_type_id/doc_sequence_id`，不能套用类型本身不存在的 `company_id/active/code` 或用空编号查询误判缺失。
- 城市淡旺季：查 `dy.expense.city.season` 的 `name/code/low_season_ids/peak_season_ids/active`；没有独立公司及生效日期字段。月份/日期定义按字段元数据返回的关联模型继续核验。
- 单据权限：查 `dy.expense.permission.configuration` 的 `company_id/document_type/is_active/effective_date/expiration_date/line_ids`，再读 `dy.expense.permission.configuration.line` 的部门、员工、提交及查询权限。`is_active` 按服务器今天计算，不是独立启用开关；目标业务日期按完整起止日期推算，缺失/异常日期另列待核实，不能当作历史快照。
- 费用单据样例：申请用 `dy.expense.requisition`、借款用 `dy.expense.payment.requisition`、还款用 `dy.expense.repayment.requisition`；按实际 `state/company_id` 过滤，读取最小字段，按公司分组核验隔离。
- 费用与总账期间：查 `dy.fin.account.period` 的 `expense_period_status` 与总账 `state`，按公司、账套和确认业务日期筛选。总账开放不代表费用开放，状态快照不证明关账检查、开关顺序或岗位权限已验证。
- 政策头与明细：先按公司、费用项目和来源明确的政策名查 `dy.expense.policy`，再用 `dy.expense.policy.line.expense_policy` 关联所选头 ID。子行继承头范围，不额外按行上可空的 `company_id` 过滤共享政策。按目标业务日期以 `effective_date <= as_of` 且 `expiry_date >= as_of` 统计有效明细，空起始/截止日分别视为无边界；同时保留全部明细数，核验城市、职称、淡旺季及 `control_level`。`warning` 与 `prohibit` 不等同，数量相符不能替代边界算例。
- 预算规则：科室预算查 `dy.budget.control.execution.rule` 的 `is_active_flag/date_from/date_to/source_doc/effective_node`；专项费用规则查 `dy.hrfm.expense.execute.rule` 的 `active_flag/date_from/date_to/source_doc/execute_node/effective_node`，并按当前费用检查适用的 `source_doc` 白名单限定，排除合同等其他业务来源规则。来源明确不启用、不续期的历史规则不要求补建；按申请/报销、节点和业务日期分别判断，子行数量或单个启用开关不足以证明主规则有效。
- OCR 席别映射：如本期交付席别识别，先核对 `ocr_expense` 实际版本及 `dy.seat.classification.mapping` 的字段元数据，再分页读取公司、票种、承运人、座位文本和席别。依据运行时匹配键重建每张票的全部候选，检查跨公司同键冲突，以及空承运人与精确承运人同时命中的重叠；不能仅按公司内重复统计判定唯一。还需用目标公司正例、跨公司反例和精确/通用承运人边界例验证实际命中，源码中的 `LIMIT 1` 且无排序不能当作唯一性保证。
