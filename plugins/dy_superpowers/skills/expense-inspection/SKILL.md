---
name: expense-inspection
description: 在巡检 Odoo 12 HRP 费用模块交付、配置、权限、费用政策、预算、付款、税务、集成或费用到会计衔接时使用。
---

# 费用模块巡检

## 目标

依据本技能和巡检矩阵，对当前工作区对应的单一项目执行费用模块交付巡检。矩阵中的“待确认”和示例不是项目事实，必须通过项目资料、Odoo 读取结果或业务确认核实。

## 工作边界

- 首期按 Odoo 12 兼容边界工作，先确认实际修订版本、版本型、已安装模块、自定义 addon、公司、数据库和目标环境。
- 只检查本期交付范围。已安装不等于已配置或已验收；后期、不交付和不适用项不能判为缺失。
- 不猜测公司的制度、预算、审批、税务或付款口径。缺少依据时标记“待确认/未知”。
- 默认只读。涉及配置修改、数据写入、安装模块、重跑凭证或生产操作时，先生成计划，经目标级授权后才能执行。
- 每次 MCP 调用必须带 `trace_id`，并记录开始、范围、检查项、证据读取、补充调研状态、异常、结论和结束事件。工具自动追踪允许的记忆/调研正文、搜索词和脱敏查询条件；不记录密码、令牌、常见个人字段值或 Odoo 返回行。未确认问答不手工写入 trace。
- 每个子项调用记忆、调研或 Odoo 工具时，附带可选 `trace_context`：`skill=expense-inspection`、`module=expense`、矩阵原始 `check_order` 和 `research_id`；已读取确认记录时再附带原样返回的 `scope_id`、`revision`。仅传已知标识，不编造范围或版本；跨轮任务沿用 `trace_id`，详见[运行边界](../../docs/runtime-boundaries.md#链路与业务标签)。
- 项目记忆仅限当前工作区。用 `dy_module_memory_read` 按检查键读取已确认的模块调研记录；新结论由 `module-research` skill 在用户确认后写入，不得把模板、推测或未确认回答当成记忆。

## Odoo 证据工具

真实 Odoo 状态需用同一 `trace_id` 的只读工具核验。每次 Odoo 调用都在 `trace_context` 中传递已确认的模块、检查顺序、子项、调研版本、`scope_id` 和环境；再用 `dy_odoo_module_list_installed` / `dy_odoo_module_info` 确认模块（模块列表每页最多 100 条，返回满页时必须继续用 offset 翻页，直到取全量后才能下“未安装”结论），用 `dy_odoo_model_fields` 确认字段，最后用 `dy_odoo_record_search` 定位配置或业务样例，并用 `dy_odoo_record_read` 读取最小必要字段。结果需回链到“检查顺序 + 调研子项编号”，只有证据足以支撑对应验收标准时才可判通过；空结果、权限不足、连接失败或字段含义不明均记为 `未知/未检查/待确认`。

`dy_knowledge_module_info` 和 `dy_knowledge_search` 只用于解释模块、模型和常见流程，不是项目事实来源，也不能覆盖实时 Odoo 返回或用户已确认的调研结论。业务记录正文不写入 trace、记忆或矩阵；trace 仅保留工具、模型、操作、数量、状态及证据引用。

## 源码对照

巡检矩阵用业务叫法（如“费用管理”“会计平台-费用”“预算会计平台-基础”“预算控制&费用报销集成”），账套中是技术模块名。完整对照见 [odoo12-source-index.md](../../references/odoo12-source-index.md)，费用域核心映射：

| 矩阵业务叫法 | 技术模块 | 关键配置模型 |
| --- | --- | --- |
| 费用管理（Dy Expense Management） | `dy_expense` | `dy.expense.document.type`、`dy.expense.item`、`dy.expense.city.season`、`dy.expense.permission.configuration` |
| 会计平台-费用 | `dy_fin_expense_pf` | `dy.fin.expense.pf`（生成规则复用 `dy_fin_pf` 的 `dy.fin.pf.account.move.generation.rule`） |
| 预算会计平台-基础（仅公立医院） | `dy_fin_budget_pf` | 无新模型，扩展预算凭证通道 |
| 预算控制&费用报销集成 | `dy_budget_control_expense` | `dy.budget.control.rule.expense.item.line`、`dy.budget.control.exp.app.line`；预算控制主规则在依赖模块 `dy_budget_control` 的 `dy.budget.control.execution.rule` |
| 专项经费管理&费用管理集成 | `dy_hrfm_expense`（依赖 `dy_hrfm`、`dy_expense`） | 扩展费用与经费衔接；经费执行规则在基础模块 `dy_hrfm` 的 `dy.hrfm.expense.execute.rule` |

常用只读查询示例（domain 仅为形状参考，须按已确认调研结论裁剪公司与范围，字段先用 `dy_odoo_model_fields` 核对）：

- 单据类型与编号：`dy.expense.document.type` 的公司范围是多对多 `company_ids`，启用字段是 `is_active`；不可套用 `company_id/active/code`。核对字段后，以 `[公司ID]` 替换实际范围，在 `dy_odoo_record_search` 中使用 `args=[[["company_ids","in",[公司ID]],["is_active","=",true]]]`，`kwargs={"fields":["id","name","company_ids","is_active","document_type"],"limit":100,"offset":0}`。编号规则查 `dy.expense.document.type.sequence` 的 `company_id/doc_type_id/doc_sequence_id`；不能用类型本身的空编号查询误判编号缺失。
- 城市淡旺季：查 `dy.expense.city.season` 的 `name/code/low_season_ids/peak_season_ids/active`。本模型没有独立公司及生效日期字段；月份/日期定义须按 `dy_odoo_model_fields` 返回的关联模型继续只读核验，不把其他配置模型的字段套用到这里。
- 单据权限（代填代提、可见范围）：查 `dy.expense.permission.configuration` 的 `company_id/document_type/is_active/effective_date/expiration_date/line_ids`，再读 `dy.expense.permission.configuration.line` 的部门、员工、提交及查询权限；按有效期和本期公司范围核对，不只看是否有记录。
- 费用单据状态样例：查 `dy.expense.requisition`（申请）、`dy.expense.payment.requisition`（借款）、`dy.expense.repayment.requisition`（还款），按 `state/company_id` 过滤，只取最小字段；公司隔离按 `company_id` 分组核对。
- 费用与总账期间：在 `dy.fin.account.period` 同时核对 `expense_period_status` 与总账 `state`，按公司、账套和确认的业务日期过滤。总账开放不代表费用开放；只读状态快照也不能证明关账检查、开关顺序或岗位权限已经验证。
- 政策范围与明细：先按公司、费用项目和来源明确的政策名定位 `dy.expense.policy`，再以 `dy.expense.policy.line.expense_policy` 关联所选头 ID；分别统计全部明细和业务日期有效明细。核对城市、职称、淡旺季及 `control_level`；`warning` 与 `prohibit` 不能等同，数量相符也不能替代边界算例。
- 预算规则：科室预算用 `dy.budget.control.execution.rule` 的 `is_active_flag/date_from/date_to/source_doc/effective_node`；专项预算用 `dy.hrfm.expense.execute.rule` 的 `active_flag/date_from/date_to/source_doc/execute_node/effective_node`。来源明确不启用、不续期的历史规则不要求补建；按申请/报销、节点和业务日期分别判断，不能用子行数量或单个启用开关代替主规则有效性。

源码版本与账套实际版本可能不同：以 `dy_odoo_module_info` 返回的 `latest_version` 为账套事实，源码索引只提供模型与依赖线索。定制 addon 可能增删字段，查询前先 `dy_odoo_model_fields`。

## 来源调研对照与端到端验证

用户提供已有调研结果（问答、Excel、Word、PDF 或接口结果）时，先读取[来源驱动巡检与端到端验证](../../references/inspection-source-validation.md)，对照 `DY-xx` 来源键、矩阵键及子项，并保留来源位置、范围、待确认项和验收标准差异。来源标准与当前矩阵不一致时，不改矩阵、不静默选一版；在报告中阻塞相关验收分支。

真实端到端验证按该参考的 live 流程显式启用，只使用 Odoo JSON-RPC 只读工具。运行范围必须来自来源中经确认的公司和业务日期；来源没给时记录待确认，不以测试日期替代业务事实。测试成功只证明 MCP 链路及只读配方可运行；没有正式岗位、正反例、边界及业务签认时，门禁保持待确认。
## 执行流程

1. **建立项目上下文**：读取当前工作区项目资料、授权来源和工作区记忆；确认项目、公司/院区、业务范围、上线日期、环境和 Odoo 版本。只复用有来源且仍有效的已确认结论。
2. **定位调研点**：读取 `references/inspection-matrix.md`。表格中每个“检查顺序”对应一个配置项；其下 J 列“调研结论”和 K 列“验收标准（调研后）”按行一一对应为子项。A:I、L:M 的纵向合并表示这些内容属于同一配置项，不能将合并区域误当作多个独立配置项。
3. **补齐调研缺口**：逐个子项核对已确认结论、适用范围、依据及确认状态。缺失、过期或含糊时，调用 `module-research` skill 进行多轮补充问答；每轮只追问待澄清点，整理答复并请顾问确认。未确认前不推断答案、不选定 K 列条件分支，也不写入记忆。只暂停依赖该结论的检查，其他独立检查继续；确认后由调研 skill 使用 `dy_research_record_write` 保存，再回到原检查项。
4. **确定适用性与验收分支**：依据 J 列已确认结论、适用条件和本期范围，为每个子项标记 `适用/不适用/后期/待确认` 及理由；只执行 K 列对应的“调研后”标准和必要的共通检查，裁剪已确认不适用的条件分支。模板中的“待确认”、示例或安装状态均不能替代项目结论。
5. **按优先级巡检**：只检查本期适用项，依次处理 P0、P1、P2；优先基础配置，再检查单据、政策、预算、审批、支付、发票、OCR、模板和会计衔接。
6. **逐子项验证**：按配置分类、配置项、配置路径、优先级和检查目的定位检查内容，结合检查要点、异常识别、J 列确认结论及对应 K 列验收标准逐点验证。每个子项单独记录状态与最小证据引用，以“检查顺序 + J/K 子项编号”回链；不得只汇总配置项或遗漏子项。
7. **验证业务样例**：对适用范围选择正例、边界例和反例；按业务覆盖公司隔离、权限、金额/日期边界、预算占用释放、借款核销、政策命中、支付和费用制证闭环。
8. **形成结论**：每项只使用 `通过/部分通过/不通过/不适用/待确认/未检查/未知`；将配置问题、资料缺口、业务决策和外部开发需求分开。
9. **回链交付对象**：把问题、证据和验证结果关联到项目需求、交付物、验收条件和必要的配置计划；不得将项目事实写回插件参考矩阵。不得写入密码、令牌、员工个人信息或生产业务明细。

巡检调用补充调研时必须传递原 `trace_id`、`检查顺序 + 子项编号`、阻塞原因、对应“验收标准（调研后）”及已知事实。调研返回后用 `dy_module_memory_read` 携带稳定 `scope_id` 复核并从 `records[0]` 获取记录，只恢复 `confirmed` 子项；`pending` 或 `needs_confirmation` 子项继续保持 `待确认/未检查`，不得因已发起问题而判通过。

## 检查重点

- 应用与依赖：费用基础应用及按需 OCR、费用制证、预算会计、科室预算、专项经费和支付集成。
- 组织与权限：公司/院区、跨公司隔离、角色分离、代填代提、审批取人、在职状态和领导关系。
- 单据与流程：申请、借款、报销、还款、付款、单据类型、编号、附件、打印、审批和财务审核。
- 预算与政策：科室/专项预算映射、占用/扣减/释放、超预算口径、城市淡旺季、费用标准、税前税后和有效期切换。
- 财务与支付：期间开关、费用制证、币种汇率、院外账户、支付方式、个税和费用到总账的结果核对。
- 集成与输出：OCR、移动端、银企、接口字段、回写状态、重试/幂等、模板版本和归档样张。

## 结果留存与复查

按 [交付记录要求](../../docs/delivery-domain.md) 为每个子项和适用范围记录本次结果，保留调研 `revision` 与 `scope_id`、对应验收标准完整原文、矩阵文件 SHA-256、带时区的验证时间和最小证据引用。跨公司不得共用结论；调研工具报不一致或范围不明确时保持“待确认”，不得绕过校验。

先按[巡检校验合同](../../docs/inspection-validation.md)列出完整模块矩阵：纳入项放入 `expected_checks`，范围外子项放入 `excluded_checks` 并说明理由。按调研结果挑选检查项时，只把调研结论已覆盖且本轮可验证的子项放入 `expected_checks`，其余子项全部进 `excluded_checks` 并写分类理由（调研缺口/需操作验证/本轮未安排），不虚构占位记录；部分范围报告的门禁只代表纳入项（校验结果标注 N/M 覆盖度），不得表述为整模块通过，规则详见[来源驱动巡检与端到端验证](../../references/inspection-source-validation.md#按调研覆盖挑选检查项部分范围巡检)。每个范围内子项都须有一条当前结果记录。调用 `dy_inspection_validate`；`valid=false` 时修正记录，不能报告门禁通过；`valid=true` 只代表记录校验通过，还须检查 `gate`。验证时间须带时区、不得晚于当前时间并晚于所用调研修订；Odoo 证据须引用同一工作区 trace 中该模型的成功读取。

向用户展示报告和校验限制并获保存确认后，调用 `dy_inspection_report_save`，提供完整报告和 `confirmed=true`，无需生成编号。工具会重新校验，并自动生成含 UTC 时间戳及唯一后缀的报告编号，按日期把**固定结构交付物**保存到 `.dy_superpowers/reports/expense/<YYYY-MM-DD>/`：业务 PDF（`<report-id>.pdf`，固定五节：一页结论/范围概览/检查结果汇总/阻塞事项与下一步/技术追溯，带页码页脚，不含机器可读 JSON）与原始 Markdown（`<report-id>.md`，含完整检查数据供复核和自动处理）。响应返回实际编号和两个交付物路径。使用响应中的模块与报告编号调用 `dy_inspection_report_read` 复核保存内容。历史 Markdown 报告缺 PDF 时，用 `dy_inspection_report_export_pdf` 重导出（数据按当前矩阵重新校验，已有 PDF 不覆盖）。调研修订、范围或标准变化后，受影响项先转为待复查；未消除的阻塞不得汇总为通过。

## 输出合同

输出中文巡检报告，至少包含：项目和环境画像、范围与适用性、按检查顺序及 J/K 子项编号对应的调研结论和巡检结果、证据引用、异常及影响、资料/确认缺口、阻塞项、建议计划和交付门禁结论。不得只报告“已检查”；必须说明未检查、未知和不适用的理由。模糊或缺失结论应列为待补充调研，并标明被阻塞的检查分支。

## 参考资料按需加载

- 巡检依据为 `references/inspection-matrix.md`；按配置项定位，并将每个 J 列调研结论与同编号 K 列验收标准配对使用。
- 新的或变更的调研结论交由 `module-research` skill 多轮确认及写入工作区记忆；巡检报告保留本次使用的结论和来源，不覆盖插件参考模板。
- 只加载当前业务范围涉及的检查分类；无法确认是否适用时先标记“待确认”，不要直接跳过。
