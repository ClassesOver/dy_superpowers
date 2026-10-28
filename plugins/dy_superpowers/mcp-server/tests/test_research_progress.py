import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from dy_superpowers_mcp.research_progress import _guide_items, calculate_progress, export_unanswered_template
from dy_superpowers_mcp.research import write_record
import dy_superpowers_mcp.research_progress as progress_module
import server


def _records(module, scope_id, answered):
    return [
        {"module": module, "check_order": item["check_order"], "research_id": item["research_id"],
         "scope": "中心院区", "scope_id": scope_id, "status": "confirmed", "research_complete": True}
        for item in _guide_items(module)[:answered]
    ]


def test_expense_progress_uses_unique_guide_subitems_and_confirmed_records_only():
    progress = calculate_progress(
        "expense", _records("expense", "site-a", 64) + [
            {"module": "expense", "check_order": "65", "research_id": "01",
             "scope_id": "site-a", "status": "retired"},
            {"module": "expense", "check_order": "1", "research_id": "01",
             "scope_id": "site-b", "status": "confirmed"},
        ], scope_id="site-a")

    assert progress["total"] == 92
    assert progress["answered"] == 64
    assert progress["percentage"] == pytest.approx(64 / 92 * 100)
    assert progress["route"] == "excel"


def test_partial_confirmed_record_does_not_count_as_answered():
    records = _records("expense", "site-a", 1)
    records[0]["research_complete"] = False
    progress = calculate_progress("expense", records, scope_id="site-a")

    assert progress["answered"] == 0
    assert progress["items"][0]["research_id"] == records[0]["research_id"]


def test_below_70_percent_exports_but_at_or_above_70_uses_interactive_questions():
    below = calculate_progress("general-ledger", _records("general-ledger", "site-a", 57), scope_id="site-a")
    above = calculate_progress("general-ledger", _records("general-ledger", "site-a", 58), scope_id="site-a")
    assert below["total"] == 82 and below["route"] == "excel"
    assert above["percentage"] >= 70 and above["route"] == "interactive"


def test_scope_ambiguity_is_rejected_instead_of_combining_companies():
    records = _records("expense", "site-a", 1) + _records("expense", "site-b", 1)
    with pytest.raises(ValueError, match="适用范围"):
        calculate_progress("expense", records)

    same_label = [
        {"module": "expense", "check_order": "1", "research_id": "01", "scope": "中心院区",
         "scope_id": "old-id", "status": "confirmed"},
        {"module": "expense", "check_order": "1", "research_id": "01", "scope": "中心院区",
         "scope_id": "new-id", "status": "confirmed"},
    ]
    with pytest.raises(ValueError, match="scope_id"):
        calculate_progress("expense", same_label, scope="中心院区")


def test_unmapped_gl_guide_question_is_not_counted_as_a_matrix_subitem():
    progress = calculate_progress("general-ledger", [], scope="中心院区")
    assert progress["total"] == 82
    assert all(item["research_id"] != "017" for item in progress["items"])


def test_progress_and_template_source_subitems_only_from_research_guide(monkeypatch, tmp_path):
    assert not hasattr(progress_module, "_matrix")
    progress = calculate_progress("expense", [], scope="中心院区")
    assert progress["total"] == 92
    export_unanswered_template(tmp_path, "expense", "trace-1", records=[],
                               scope="中心院区", confirmed=True)


def test_export_requires_confirmation_and_never_overwrites_or_follows_symlinks(tmp_path):
    with pytest.raises(PermissionError):
        export_unanswered_template(tmp_path, "expense", "trace-1", records=[], scope="中心院区", confirmed=False)

    result = export_unanswered_template(tmp_path, "expense", "trace-1", records=[], scope="中心院区", confirmed=True)
    output = Path(result["path"])
    actual_output = tmp_path / output
    assert output == Path(".dy_superpowers/research/expense/unanswered-questions.xlsx")
    assert actual_output.is_file()
    with pytest.raises(FileExistsError):
        export_unanswered_template(tmp_path, "expense", "trace-2", records=[], scope="中心院区", confirmed=True)


def test_export_contains_only_unanswered_rows_and_refuses_directory_symlinks(tmp_path):
    records = _records("expense", "site-a", 64)
    result = export_unanswered_template(tmp_path, "expense", "trace-1", records=records,
                                        scope="中心院区", scope_id="site-a", confirmed=True)
    from openpyxl import load_workbook
    workbook = load_workbook(tmp_path / result["path"], read_only=True)
    assert workbook.active.max_row == 29

    import shutil
    shutil.rmtree(tmp_path / ".dy_superpowers")
    other = tmp_path / "other"
    other.mkdir()
    (tmp_path / ".dy_superpowers").symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError, match="符号链接"):
        export_unanswered_template(tmp_path, "expense", "trace-2", records=[],
                                  scope="中心院区", confirmed=True)


def test_mcp_tools_calculate_scoped_progress_and_require_export_confirmation(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    item = _guide_items("expense")[0]
    write_record(workspace, {"module": "expense", "check_order": item["check_order"],
                             "research_id": item["research_id"], "config_item": item["config_item"],
                             "conclusion": "已确认", "scope": "中心院区", "confirmed_by": "test",
                             "confirmed_at": "2026-01-01", "confirmed": True, "scope_id": "site-a",
                             "research_complete": True})

    progress, error = server.execute_tool("dy_research_progress", {
        "trace_id": "progress-test", "module": "expense", "scope_id": "site-a"}, workspace)
    assert error is None
    assert progress["answered"] == 1 and progress["total"] == 92
    assert progress["route"] == "excel"

    _, error = server.execute_tool("dy_research_unanswered_export", {
        "trace_id": "progress-test", "module": "expense", "scope_id": "site-a", "confirmed": False}, workspace)
    assert error and "明确确认" in error["message"]

    exported, error = server.execute_tool("dy_research_unanswered_export", {
        "trace_id": "progress-test", "module": "expense", "scope_id": "site-a", "confirmed": True}, workspace)
    assert error is None
    assert exported["question_count"] == 91


def test_mcp_progress_without_scope_returns_scope_requirement_instead_of_failing(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    result, error = server.execute_tool("dy_research_progress", {
        "trace_id": "progress-scope", "module": "expense"}, workspace)
    assert error is None
    assert result["route"] == "needs_scope"
    assert result["needs_scope"] is True
