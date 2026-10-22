"""Odoo 只读查询的字段最小化与敏感字段拦截。"""
import pytest

import server
from dy_superpowers_mcp.odoo_jsonrpc import OdooConfigurationError, check_field_access


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(server, "initialize_telemetry", lambda: False)
    for name, value in {"ODOO_URL": "http://odoo", "ODOO_DB": "db", "ODOO_USERNAME": "u", "ODOO_PASSWORD": "p"}.items():
        monkeypatch.setenv(name, value)

    def must_not_connect(self, settings):
        raise AssertionError("字段策略应先于 Odoo 连接")

    monkeypatch.setattr(server.OdooReadClient, "__init__", must_not_connect)
    return project


@pytest.mark.parametrize("tool, arguments", [
    ("dy_odoo_record_search", {"model": "account.move", "args": [[]], "kwargs": {"limit": 5}}),
    ("dy_odoo_read", {"model": "account.move", "kwargs": {"fields": []}}),
    ("dy_odoo_record_read", {"model": "account.move", "ids": [1]}),
    ("dy_odoo_record_read", {"model": "account.move", "ids": [1], "fields": []}),
])
def test_odoo_reads_require_explicit_fields(workspace, tool, arguments):
    result, error = server.execute_tool(tool, {"trace_id": "fields-required", **arguments}, workspace)
    assert result is None
    assert error["code"] == -32602
    assert "fields" in error["message"]


@pytest.mark.parametrize("model, fields", [
    ("res.users", ["login", "password"]),
    ("res.users", ["api_key_ids"]),
    ("hr.employee", ["name", "identification_id"]),
    ("hr.employee", ["passport_id"]),
    ("hr.employee", ["bank_account_id"]),
    ("hr.employee", ["birthday"]),
    ("hr.employee", ["private_email"]),
    ("hr.employee", ["mobile_phone"]),
    ("res.partner", ["email"]),
    ("res.partner", ["street2"]),
    ("hr.applicant", ["email_from"]),
    ("hr.employee", ["work_email"]),
    ("res.partner.bank", ["acc_number"]),
])
def test_sensitive_fields_are_rejected(model, fields):
    with pytest.raises(OdooConfigurationError, match="敏感字段"):
        check_field_access(model, fields, [])


@pytest.mark.parametrize("domain", [
    [["identification_id", "=", "110101199001011234"]],
    ["|", ["name", "=", "A"], ["employee_id.identification_id", "ilike", "1101"]],
    [["user_id.password", "!=", False]],
    [["employee_id.work_phone", "=", "123"]],
])
def test_sensitive_fields_in_domain_are_rejected(domain):
    with pytest.raises(OdooConfigurationError, match="敏感字段"):
        check_field_access("hr.expense", ["name"], domain)


def test_business_fields_are_allowed():
    check_field_access("account.move", ["name", "state", "journal_id", "partner_id", "amount_total"], [["state", "=", "posted"], "|", ["company_id", "=", 1], ["company_id", "=", False]])
    check_field_access("hr.employee", ["name", "department_id", "job_id", "company_id"], [])
    check_field_access("hr.expense", ["name", "employee_id", "total_amount", "state"], [["employee_id.department_id", "=", 3]])
    # 通讯字段只在人员相关模型上拦截；业务单据上的同名字段不受影响。
    check_field_access("account.journal", ["name", "type"], [])
    # 通讯字段按下划线分词匹配，不误伤包含相同子串的业务字段。
    check_field_access("res.partner", ["name", "capacity_total", "zipped_flag"], [["move_id.velocity", ">", 0]])


def test_sensitive_field_rejection_happens_through_tools(workspace):
    result, error = server.execute_tool(
        "dy_odoo_record_read",
        {"trace_id": "fields-sensitive", "model": "hr.employee", "ids": [1], "fields": ["name", "identification_id"]},
        workspace,
    )
    assert result is None
    assert error["code"] == -32602
    assert "identification_id" in error["message"]
