"""巡检配方使用源码归属字段，不以界面候选公司或今天状态替代业务范围。"""

import pytest

import test_inspection_source_e2e as recipes


@pytest.mark.parametrize("model", [
    "dy.fin.account.analytic.plan", "dy.fin.cash.flow.mapping", "account.mapping",
])
def test_owner_company_wins_over_nonsearchable_candidate_companies(model):
    metadata = {
        "company_id": {"type": "many2one", "relation": "res.company", "store": True},
        "company_ids": {"type": "many2many", "relation": "res.company",
                        "store": False, "searchable": False},
    }
    assert recipes.scoped_domain(model, metadata, [7], [3]) == [["company_id", "in", [7]]]


def test_journal_scope_includes_shared_journals_only_in_selected_structures():
    metadata = {
        "company_id": {"type": "many2one", "relation": "res.company", "required": False},
        "account_structure_id": {"type": "many2one", "relation": "dy.fin.account.structure"},
        "journal_company_ids": {"type": "many2many", "relation": "res.company"},
    }
    assert recipes.scoped_domain("account.journal", metadata, [7], [3]) == [
        ["account_structure_id", "in", [3]],
        "|", ["journal_company_ids", "=", False], ["journal_company_ids", "in", [7]],
    ]
    # 没有结构时也必须限定为空结果，不能退回只按公司或无界限查询。
    assert recipes.scoped_domain("account.journal", metadata, [7], [])[0] == [
        "account_structure_id", "in", []]


def test_document_type_scope_includes_global_types():
    metadata = {"company_ids": {"type": "many2many", "relation": "res.company"}}
    assert recipes.scoped_domain("dy.expense.document.type", metadata, [7], []) == [
        "|", ["company_ids", "=", False], ["company_ids", "in", [7]],
    ]


@pytest.mark.parametrize("missing", ["account_structure_id", "journal_company_ids"])
def test_journal_scope_rejects_missing_real_scope_fields(missing):
    metadata = {"company_id": {}, "account_structure_id": {}, "journal_company_ids": {}}
    del metadata[missing]
    with pytest.raises(ValueError, match="范围字段"):
        recipes.scoped_domain("account.journal", metadata, [7], [3])


def test_policy_lines_use_selected_parent_even_when_line_has_company_field():
    metadata = {
        "company_id": {"type": "many2one", "relation": "res.company", "required": False},
        "expense_policy": {"type": "many2one", "relation": "dy.expense.policy"},
    }
    # source_workflow 另追加 expense_policy in 已限定范围的政策头。
    assert recipes.scoped_domain("dy.expense.policy.line", metadata, [7], []) == []


def test_permission_validity_uses_business_date_not_today_computed_flag():
    rows = [
        {"effective_date": "2024-01-01", "expiration_date": "2024-12-31", "is_active": False},
        {"effective_date": "2026-01-01", "expiration_date": "2028-12-31", "is_active": True},
    ]
    result = recipes.permission_validity(rows, "2024-06-01")
    assert result["validity"] == {"within_date_range": 1, "future": 1}
    assert result["current_date_state"] == {"is_active_true": 1, "is_active_false": 1}
    future = recipes.permission_validity(rows, "2027-06-01")
    assert future["validity"] == {"expired": 1, "within_date_range": 1}
