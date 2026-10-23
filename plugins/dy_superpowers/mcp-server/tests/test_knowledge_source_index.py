"""源码导出的模块知识索引测试（数据来自 HRP Odoo 12 源码仓库）。"""
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(PLUGIN_ROOT / "mcp-server/src"))

from dy_superpowers_mcp.knowledge import MODULES, module_info, search


def test_inspection_domain_modules_carry_source_facts():
    expense = module_info("dy_expense")
    assert expense["found"] is True
    assert expense["name"] == "费用管理"
    assert expense["depends"] == ["dy_fin_mdm"]
    assert expense["source_repo"] == "dy-expense"
    assert "dy.expense.document.type" in expense["models"]
    assert "dy.expense.requisition" in expense["models"]

    mdm = module_info("dy_fin_mdm")
    assert mdm["depends"] == ["dy_base", "account_accountant", "dy_base_data"]
    assert "dy.fin.account.period.open.and.done" in mdm["models"]
    assert "dy.fin.cash.flow.mapping" in mdm["models"]

    gaap = module_info("dy_fin_gaap")
    assert "account.mapping" in gaap["models"]
    assert "budget.account.move" in gaap["models"]


def test_knowledge_records_distinction_between_source_and_standard_modules():
    sourced = module_info("dy_fin_acc")
    assert sourced["source_repo"] == "dy-finance"
    assert "source" not in sourced  # 源码事实条目不冒充标准模块摘要
    standard = module_info("account")
    assert standard["source"] == "Odoo 12 标准模块概念摘要"


def test_chinese_alias_search_matches_real_modules():
    assert {row["module"] for row in search("报销")["results"]} >= {"dy_expense"}
    assert {row["module"] for row in search("平行记账")["results"]} >= {"dy_fin_gaap"}
    assert {row["module"] for row in search("会计期间")["results"]} >= {"dy_fin_mdm"}
    assert {row["module"] for row in search("费用制证")["results"]} >= {"dy_fin_expense_pf"}


def test_unknown_module_lists_available_domains():
    result = module_info("dy_nonexistent")
    assert result["found"] is False
    assert "dy_expense" in result["available"]
