# 费用配置定位

以本次需求选择检索词，不按本表顺序执行。

| 需求叫法 | 首轮检索词/技术线索 | 实时核对重点 |
| --- | --- | --- |
| 报销单据类型、编号 | 单据类型、dy.expense.document.type、dy.expense.document.type.sequence | company_ids 与 company_id 不同；主表不可假设有 code/active；核验 is_active 及编号关联 |
| 费用政策、季节 | dy.expense.policy、dy.expense.policy.line、dy.expense.city.season | 政策头与子行、公司范围、业务日期、季节与城市关系 |
| 费用项目、控制规则 | dy.expense.item、dy_budget_control_expense | 关联项目存在及适用公司，安装状态与控制规则有效范围 |
| 费用制证 | dy_fin_expense_pf、dy.fin.pf.account.move.generation.rule | 当前安装版本、规则条件及影响因素，不执行制证操作 |

这些模型是检索线索，不是写入白名单。白名单以 MCP schema/服务端合同为准。查询发现缺少前置时，读取相关源码与元数据，再判断需复用、补配置还是补充确认。
