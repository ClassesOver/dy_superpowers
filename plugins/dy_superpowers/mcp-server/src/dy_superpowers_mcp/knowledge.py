"""HRP/Odoo 项目的本地模块知识索引。

数据来源：鼎医 HRP Odoo 12 源码仓库（dy-expense、dy-finance、dy-acc-center、
dy-budget、dy-hrfm、dy-base 等）的 __manifest__.py 与模型声明；
技术名、模型名和依赖为源码事实，工作流描述为通用概括。源码索引详见插件
references/hrp-source-index.md。线上账套的实际安装状态、字段和定制必须通过
dy_odoo_* 工具核验；本索引只提供解释和检索线索。
"""
from __future__ import annotations
from typing import Any

MODULES: dict[str, dict[str, Any]] = {
    # ---- 费用巡检域（源码仓库 dy-expense / dy-acc-center / dy-budget / dy-hrfm） ----
    "dy_expense": {
        "name": "费用管理",
        "aliases": ["费用", "报销", "差旅", "借款", "还款", "费用单据"],
        "models": [
            "dy.expense.requisition", "dy.expense.payment.requisition", "dy.expense.repayment.requisition",
            "dy.expense.document.type", "dy.expense.document.type.sequence",
            "dy.expense.item", "dy.expense.application.item", "dy.expense.city.season",
            "dy.expense.permission.configuration", "dy.expense.approval.task",
            "dy.expense.credit.score.config", "dy.expense.detail.report",
            "dy.expense.report", "dy.expense.report.payment",
            "dy.expense.policy", "dy.expense.policy.line",
        ],
        "workflow": "申请 → 借款/报销 → 审批 → 付款/核销；单据类型、编号和权限由 dy.expense.document.type 与 dy.expense.permission.configuration 配置",
        "model_notes": {
            "dy.expense.document.type": "公司范围为 company_ids（Many2many），启用字段为 is_active；主表没有独立 code 字段，不要套用 company_id/active/code",
            "dy.expense.document.type.sequence": "编号分配用 company_id/doc_type_id/doc_sequence_id 核对",
            "dy.expense.city.season": "low_season_ids/peak_season_ids/active；本模型无独立公司和有效期字段，季节关联本身不能证明月份与政策覆盖",
            "dy.expense.policy": "公司范围可同时有 company_id 与 company_ids；明细 dy.expense.policy.line 用 city_id/season_type/effective_date/start_date/end_date/expiry_date/control_level 等核对适用政策",
        },
        "depends": ["dy_fin_mdm"],
        "source_repo": "dy-expense",
    },
    "ocr_expense": {
        "name": "OCR&费用管理集成",
        "aliases": ["OCR", "发票识别", "费用OCR"],
        "models": ["dy.ocr.expense.item.rule", "dy.expense.book", "dy.expense.report"],
        "workflow": "源码含 OCR 费用规则、费用台账和报销单扩展；实际识别与审批流程须在目标账套核验",
        "depends": ["ocr_base", "dy_mobile_base", "mobile_app_approval", "dy_expense", "mobile_app_fill", "mobile_expense"],
        "source_repo": "dy-mobile-service",
    },
    "dy_bank_base": {
        "name": "银医基础平台",
        "aliases": ["银医", "银企", "银行接口", "银行付款"],
        "models": ["dy.bank.payment.order", "dy.bank.payment.order.line"],
        "workflow": "源码提供银医付款单模型；银行开通、支付能力和线上流程须在目标账套核验",
        "depends": ["dy_base", "dy_fin_mdm"],
        "source_repo": "dy-bank-payment",
    },
    "dy_bank_expense": {
        "name": "银医平台&费用报销集成",
        "aliases": ["银医", "银企费用", "费用银行付款"],
        "models": ["dy.bank.payment.order", "dy.expense.report", "dy.expense.report.payment"],
        "workflow": "源码扩展银医付款单、费用报销单与付款单；模块依赖不证明线上付款链路已启用",
        "depends": ["dy_bank_base", "dy_expense"],
        "source_repo": "dy-bank-payment",
    },
    "mobile_expense": {
        "name": "移动端费用个性化",
        "aliases": ["移动费用", "移动端费用", "移动报销"],
        "models": ["dy.expense.report.mobile.iface", "dy.expense.payment.requisition.mobile.iface"],
        "workflow": "源码含费用报销与借款申请移动接口；实际移动入口与审批配置须在目标账套核验",
        "depends": ["dy_mobile_base", "mobile_app_fill", "mobile_app_approval", "dy_expense"],
        "source_repo": "dy-mobile-service",
    },
    "dy_purchase": {
        "name": "鼎医采购管理",
        "aliases": ["鼎医采购", "HRP采购", "采购管理", "采购申请"],
        "models": ["dy.purchase.requisition.order", "dy.purchase.requisition.order.line", "purchase.order"],
        "workflow": "源码定义采购申请并扩展采购订单；实际采购流程须在目标账套核验",
        "depends": ["dy_scm_mdm"],
        "source_repo": "dy-scm",
    },
    "dy_fin_expense_pf": {
        "name": "费用财务会计平台（会计平台-费用）",
        "aliases": ["费用制证", "会计平台费用", "费用凭证"],
        "models": ["dy.fin.expense.pf"],
        "workflow": "按 dy.fin.pf.account.move.generation.rule 生成规则把费用单据生成财务凭证",
        "depends": ["dy_expense", "dy_fin_pf"],
        "source_repo": "dy-acc-center",
    },
    "dy_fin_budget_expense_pf": {
        "name": "费用管理预算会计平台（仅公立医院）",
        "aliases": ["费用预算会计", "预算会计平台费用"],
        "models": [],
        "workflow": "在费用财务会计平台之上叠加预算会计记账（平行记账）；自身无新模型，扩展费用单据与预算凭证衔接",
        "depends": ["dy_fin_expense_pf", "dy_fin_budget_pf"],
        "source_repo": "dy-acc-center",
    },
    "dy_budget_control_expense": {
        "name": "预算控制&费用报销集成",
        "aliases": ["费用预算控制", "预算占用", "预算扣减"],
        "models": ["dy.budget.control.execution.rule", "dy.budget.control.rule.expense.item.line", "dy.budget.control.exp.app.line"],
        "workflow": "费用预算控制主规则在 dy_budget_control；费用集成提供费用项目控制行。须按 source_doc、company_id、date_from/date_to、is_active_flag、effective_node 及 expense_item_ids 组合核对",
        "depends": ["dy_budget_control", "dy_expense"],
        "source_repo": "dy-budget",
    },
    "dy_hrfm": {
        "name": "科研管理（专项经费）",
        "aliases": ["科研", "专项经费", "经费", "课题"],
        "models": [
            "dy.hrfm.funds.application", "dy.hrfm.budget.project", "dy.hrfm.funds.account",
            "dy.hrfm.expense.execute.rule", "dy.hrfm.budget.adjust",
        ],
        "workflow": "科研项目立项 → 经费预算 → 经费申请/认领 → 费用执行 → 结题",
        "depends": ["dy_base", "dy_base_data", "dy_fin_mdm"],
        "source_repo": "dy-hrfm",
    },
    "dy_hrfm_expense": {
        "name": "科研管理&费用管理集成",
        "aliases": ["专项经费管理&费用管理集成", "科研费用集成", "经费费用衔接"],
        "models": ["dy.hrfm.expense.execute.rule"],
        "workflow": "集成模块扩展科研与费用衔接；执行规则模型由基础模块 dy_hrfm 定义，基础模块已安装不代表集成已安装",
        "depends": ["dy_hrfm", "dy_expense"],
        "source_repo": "dy-hrfm",
    },
    # ---- 总账巡检域（源码仓库 dy-finance / dy-acc-center） ----
    "dy_fin_mdm": {
        "name": "总账主数据（财务会计-基础）",
        "aliases": ["主数据", "科目", "会计期间", "辅助核算", "现金流映射", "科目权限"],
        "models": [
            "dy.fin.account.structure", "dy.fin.account.type", "dy.fin.account.period",
            "dy.fin.account.period.open.and.done", "dy.fin.account.period.template",
            "dy.fin.account.analytic.plan", "dy.fin.cash.flow.mapping",
            "dy.account.permissions.settings", "dy.account.permissions.account",
        ],
        "workflow": "科目结构/类型 → 会计期间模板与公司分配 → 期间开关 → 辅助核算与现金流映射",
        "model_notes": {
            "dy.fin.account.period": "持久模型；以 company_id/date_from/date_to/account_structure_id 限定范围，state/state_show/cashier_period_state 为状态线索",
            "dy.fin.account.period.open.and.done": "TransientModel 操作向导，不是期间状态事实源；只读巡检不得创建向导或调用开关账方法",
        },
        "depends": ["dy_base", "account_accountant", "dy_base_data"],
        "source_repo": "dy-finance",
    },
    "dy_fin_acc": {
        "name": "总账-凭证管理",
        "aliases": ["凭证", "总账凭证", "过账", "结转", "账簿"],
        "models": [
            "account.move", "account.move.line", "dy.fin.acc.account.periodicity.voucher",
            "account.move.generate", "dy.fin.acc.account.move.summary",
            "dy.fin.acc.account.move.discrete.number",
        ],
        "workflow": "凭证：草稿 → 审核签字 → 过账 → 反过账/作废；期末结转通过结转项目、模板和执行记录生成财务或预算凭证，周期凭证模板用于经常性凭证",
        "depends": ["dy_fin_mdm"],
        "source_repo": "dy-finance",
    },
    "dy_fin_gaap": {
        "name": "总账-预算会计",
        "aliases": ["预算会计", "平行记账", "预算凭证", "预算科目映射", "收支表"],
        "models": [
            "account.mapping", "budget.account.move", "budget.account.move.line",
            "budget.account.balance.sheet", "budget.statement.of.income.and.expenditure",
            "budget.move.paralleled.wizard",
        ],
        "workflow": "财务/预算科目映射（account.mapping）→ 预算凭证平行记账 → 预算报表与结转",
        "depends": ["dy_fin_acc"],
        "source_repo": "dy-finance",
    },
    "dy_fin_pf": {
        "name": "财务会计平台-基础",
        "aliases": ["会计平台", "凭证生成规则", "凭证分类"],
        "models": [
            "dy.fin.pf.account.class.define", "dy.fin.pf.account.move.generation.rule",
            "dy.fin.pf.condition.source.mapping", "dy.fin.pf.influence.factor.setting",
            "dy.fin.voucher.generate.program",
        ],
        "workflow": "凭证分类定义 → 生成规则与条件映射 → 影响因素 → 按业务单据生成凭证",
        "depends": ["dy_fin_acc"],
        "source_repo": "dy-acc-center",
    },
    "dy_fin_budget_pf": {
        "name": "预算会计平台-基础（仅公立医院）",
        "aliases": ["预算会计平台", "预算平台基础"],
        "models": [],
        "workflow": "在财务会计平台与预算会计之上提供预算凭证生成通道；自身无新模型",
        "depends": ["dy_fin_pf", "dy_fin_gaap"],
        "source_repo": "dy-acc-center",
    },
    # ---- Odoo 12 标准基座（概念摘要，非源码仓库内容） ----
    "account": {"name": "财务会计（Odoo 12 标准模块）", "aliases": ["财务", "会计", "总账", "发票"], "models": ["account.move", "account.move.line", "account.journal", "account.account"], "workflow": "会计分录：草稿 → 已过账；付款与核销需单独验证", "depends": ["base"]},
    "hr": {"name": "人力资源", "aliases": ["人事", "员工", "HR"], "models": ["hr.employee", "hr.department", "hr.job"], "workflow": "员工主数据维护", "depends": ["base"]},
    "purchase": {"name": "采购", "aliases": ["采购", "供应商", "采购订单"], "models": ["purchase.order", "purchase.order.line"], "workflow": "询价单 → 采购订单 → 收货 → 开票", "depends": ["account"]},
}


def module_info(module: str) -> dict[str, Any]:
    info = MODULES.get(module)
    if info is None:
        return {"found": False, "module": module, "available": sorted(MODULES)}
    result = {"found": True, "module": module, "odoo_version": "12.0",
              "limitations": "源码索引仅供解释；依赖为 manifest 源码声明，不证明线上安装状态或工作流；HRP 自定义字段、定制 addon、实际安装状态和版本必须通过 dy_odoo_* 工具在目标账套核验",
              **info}
    if "source_repo" not in info:
        result["source"] = "Odoo 12 标准模块概念摘要"
    return result


def search(query: str) -> dict[str, Any]:
    query = query.strip().lower()
    results = []
    for key, info in MODULES.items():
        haystack = " ".join([key, info["name"], *info["aliases"], *info["models"]]).lower()
        if query and query in haystack:
            results.append({"module": key, "name": info["name"], "models": info["models"]})
    return {"query": query, "results": results, "count": len(results)}
