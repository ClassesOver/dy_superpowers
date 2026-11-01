---
name: general-ledger-inspection
description: 在巡检 Odoo 12 HRP 总账交付、配置、岗位权限、业务流程或会计衔接时使用。
---

# 总账模块巡检

读取当前业务范围的最新确认答卷及附件，利用现有知识检索和 Odoo 只读工具，核对当前系统是否满足答卷要求。按[巡检通用流程](../../references/inspection-workflow.md)执行，本文件只列总账领域差异。

当前总账模板提供问题范围，同范围最新确认答案和绑定附件定义业务要求。模块标识general-ledger，完整覆盖当前模板的全部question_id，分母取自prepare返回的expected_checks，原分组和编号用于展示，内部唯一question_id不改造或补号。

## 领域重点

技术配方见[总账源码与查询](references/source-queries.md)。重点核对：会计期间与开关账状态、科目结构与辅助核算、分类账、财务/预算映射与平行记账、凭证状态与借贷平衡、期末结转的回链凭证、经常性凭证，以及业务凭证生成规则。
