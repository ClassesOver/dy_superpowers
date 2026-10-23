"""HRP/Odoo 项目的本地模块知识索引。

数据来源：鼎医 HRP Odoo 12 源码仓库（dy-expense、dy-finance、dy-acc-center、
dy-budget、dy-hrfm、dy-base 等）的 __manifest__.py 与模型声明；
技术名、模型名和依赖为源码事实，工作流描述为通用概括。源码索引详见插件
references/odoo12-source-index.md。线上账套的实际安装状态、字段和定制必须通过
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
        ],
        "workflow": "申请 → 借款/报销 → 审批 → 付款/核销；单据类型、编号和权限由 dy.expense.document.type 与 dy.expense.permission.configuration 配置",
        "depends": ["dy_fin_mdm"],
        "source_repo": "dy-expense",
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
        "models": ["dy.budget.control.rule.expense.item.line", "dy.budget.control.exp.app.line"],
        "workflow": "费用申请/报销按预算控制规则占用、扣减和释放预算",
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
        "workflow": "凭证：草稿 → 审核签字 → 过账 → 反过账/作废；期末按周期凭证模板结转",
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
              "limitations": "源码索引仅供解释；HRP 自定义字段、定制 addon、实际安装状态和版本必须通过 dy_odoo_* 工具在目标账套核验",
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
