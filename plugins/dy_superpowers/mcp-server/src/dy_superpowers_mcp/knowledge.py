"""HRP/Odoo 项目的本地模块知识索引。"""
from __future__ import annotations
from typing import Any

MODULES: dict[str, dict[str, Any]] = {
    "account": {"name": "财务会计", "aliases": ["财务", "会计", "总账", "发票"], "models": ["account.move", "account.move.line", "account.journal", "account.account"], "workflow": "会计分录：草稿 → 已过账；付款与核销需单独验证", "depends": ["base"]},
    "hr_expense": {"name": "费用", "aliases": ["费用", "报销", "差旅"], "models": ["hr.expense", "hr.expense.sheet"], "workflow": "草稿 → 提交 → 审批 → 已支付", "depends": ["hr", "account"]},
    "hr": {"name": "人力资源", "aliases": ["人事", "员工", "HR"], "models": ["hr.employee", "hr.department", "hr.job"], "workflow": "员工主数据维护", "depends": ["base"]},
    "purchase": {"name": "采购", "aliases": ["采购", "供应商", "采购订单"], "models": ["purchase.order", "purchase.order.line"], "workflow": "询价单 → 采购订单 → 收货 → 开票", "depends": ["account"]},
}

def module_info(module: str) -> dict[str, Any]:
    info = MODULES.get(module)
    return {"found": False, "module": module, "available": sorted(MODULES)} if info is None else {"found": True, "module": module, "odoo_version": "12.0", "source": "Odoo 12 标准模块概念摘要", "limitations": "仅供解释；HRP 自定义模块、字段和依赖须通过目标环境核验", **info}

def search(query: str) -> dict[str, Any]:
    query = query.strip().lower()
    results = []
    for key, info in MODULES.items():
        haystack = " ".join([key, info["name"], *info["aliases"], *info["models"]]).lower()
        if query and query in haystack:
            results.append({"module": key, "name": info["name"], "models": info["models"]})
    return {"query": query, "results": results, "count": len(results)}
