---
name: general-ledger-inspection
description: 在巡检 Odoo 12 HRP 总账交付、配置、岗位权限、业务流程或会计衔接时使用。
---

# 总账模块巡检

读取当前业务范围的最新确认答卷及附件，利用现有知识检索和Odoo只读工具，核对当前系统是否满足答卷要求。连接由MCP从当前环境变量取得，不向用户重复索要。

当前总账模板提供问题范围，同范围最新确认答案和绑定附件定义业务要求。模块标识general-ledger，完整覆盖当前模板的全部question_id，分母取自prepare返回的expected_checks，原分组和编号用于展示，内部唯一question_id不改造或补号。不使用静态巡检矩阵，是否对齐须由工具检索当前系统取证后判断。

按[通用流程](../../references/inspection-workflow.md)执行“最新答卷 → 环境连接 → 提取要求 → 知识检索 → 系统查询 → 对齐比较”，报告遵循[校验合同](../../docs/inspection-validation.md)。dy_odoo_environment_info核对环境变量配置，prepare省略environment自动复用；不打印连接配置或凭据。公司或日期缺失只阻塞依赖分支，独立检查继续。

调用dy_module_memory_read默认新QA，再dy_inspection_prepare(module, scope_id, environment)，以返回basis和全部expected_checks准备核验。局部答案、缺附件或漂移保留待确认/未检查。缺口交module-research，携带真实question_id、版本和事实，批次确认保存后回读，仅恢复对应分支。

[源码与查询](references/source-queries.md)仅技术配方。先用dy_knowledge_search/read理解相关模块、模型和规则，再核对实际安装模块与字段，按当前答卷的预期值逐项查询比较。公司隔离、岗位、金额日期边界、预算、凭证、支付制证须对应业务效果证据，配置存在不能证明操作成功，无法验证保留材料/人工缺口。

从答案与附件提取requirements，按通用流程填写verification_target；附件source_refs保留当前确认绑定的reference和sha256，摘要不一致先重新确认，不能沿用旧证据。prepare返回的detail_checks逐项保留，未核验不省略。配置项用当前系统取证，调研附件不自证对齐。逐项记录预期、实值、范围及分页完整性，结构校验不证明业务语义完整。

先dy_inspection_validate，展示当前模板全部问题的覆盖、门禁与边界，用户确认保存后dy_inspection_report_save(confirmed=true)，再dy_inspection_report_read。未知项不能删除凑通过，不适用须完整答案和真实依据。模板、答案版本或附件变化重新prepare；旧报告仅历史read，不能新验证放行或冒称补PDF通过。

trace_context复用basis（含服务生成的connection_fingerprint），补question_id、answer_revision及必要的requirement_id；内部标识不展示。Odoo证据须本工作区成功tool.end和对应odoo.read。巡检只用只读工具，不调用配置或模块变更。详见[来源核对](../../references/inspection-source-validation.md)。
