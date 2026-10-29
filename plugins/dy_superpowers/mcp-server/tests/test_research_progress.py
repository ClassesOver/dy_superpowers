import sys
from contextlib import closing
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from dy_superpowers_mcp.research_progress import (
    _guide_items, calculate_progress, calculate_routing_progress, export_unanswered_template,
)
from dy_superpowers_mcp.research import write_record
import dy_superpowers_mcp.research_progress as progress_module
import server


def _records(module, scope_id, answered):
    return [
        {"module": module, "check_order": item["check_order"], "research_id": item["research_id"],
         "scope": "中心院区", "scope_id": scope_id, "status": "confirmed", "research_complete": True}
        | {"conclusion": "仅测试：已确认要素齐备"}
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


def test_five_confirmed_partial_gl_answers_remain_visible_without_claiming_completion():
    records = _records("general-ledger", "site-a", 5)
    for record in records:
        record["research_complete"] = False

    progress = calculate_routing_progress("general-ledger", records)

    assert progress["confirmed_count"] == 5
    assert progress["confirmed_percentage"] == pytest.approx(5 / 82 * 100)
    assert progress["partial_count"] == 5
    assert progress["unknown_count"] == 0
    assert progress["unanswered_count"] == 77
    assert (progress["answered"], progress["percentage"], progress["route"]) == (0, 0, "excel")
    assert "5/82" in progress["progress_summary"]
    assert "6.1%" in progress["progress_summary"]
    assert "0/82" in progress["progress_summary"]


def test_progress_separates_complete_partial_unknown_and_unanswered_subitems():
    records = _records("general-ledger", "site-a", 5)
    records[1]["research_complete"] = False
    records[2].pop("research_complete")
    records[3]["status"] = "retired"
    records[4]["check_order"] = "legacy"
    records.append(dict(records[0]))

    progress = calculate_progress("general-ledger", records, scope_id="site-a")

    assert progress["confirmed_count"] == 3
    assert progress["answered"] == 1
    assert progress["partial_count"] == 1
    assert progress["unknown_count"] == 1
    assert progress["unanswered_count"] == 79
    assert len(progress["completeness_unknown"]) == 1
    assert len(progress["unmapped_records"]) == 1


def test_multiple_scopes_report_confirmed_answers_without_combining_or_hiding_them():
    records = _records("general-ledger", "site-a", 5) + _records("general-ledger", "site-b", 2)
    for record in records[:5]:
        record["research_complete"] = False

    progress = calculate_routing_progress("general-ledger", records)

    assert progress["scope_mode"] == "multiple"
    assert progress["confirmed_count"] == 0
    assert [(row["scope_id"], row["confirmed_count"], row["partial_count"], row["answered"])
            for row in progress["scope_progress"]] == [("site-a", 5, 5, 0), ("site-b", 2, 0, 2)]
    assert all("progress_summary" in row for row in progress["scope_progress"])
    assert "未选定范围" in progress["progress_summary"]
    assert "0/82" not in progress["progress_summary"]


def test_below_70_percent_exports_but_at_or_above_70_uses_interactive_questions():
    below = calculate_progress("general-ledger", _records("general-ledger", "site-a", 57), scope_id="site-a")
    above = calculate_progress("general-ledger", _records("general-ledger", "site-a", 58), scope_id="site-a")
    assert below["total"] == 82 and below["route"] == "excel"
    assert above["percentage"] >= 70 and above["route"] == "interactive"


def test_confirmed_coverage_above_70_percent_does_not_override_completion_routing():
    records = _records("general-ledger", "site-a", 58)
    for record in records:
        record["research_complete"] = False

    progress = calculate_progress("general-ledger", records, scope_id="site-a")

    assert progress["confirmed_count"] == 58
    assert progress["confirmed_percentage"] == pytest.approx(58 / 82 * 100)
    assert progress["answered"] == 0 and progress["percentage"] == 0
    assert progress["route"] == "excel"


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
    assert output.parent == Path(".dy_superpowers/research/expense")
    assert output.name.startswith("费用调研清单") and output.name.endswith("01.xlsx")
    assert actual_output.is_file()
    original = actual_output.read_bytes()
    next_result = export_unanswered_template(tmp_path, "expense", "trace-2", records=[], scope="中心院区", confirmed=True)
    assert next_result["path"] != result["path"]
    assert actual_output.read_bytes() == original


def test_export_keeps_answered_rows_and_refuses_directory_symlinks(tmp_path):
    records = _records("expense", "site-a", 64)
    result = export_unanswered_template(tmp_path, "expense", "trace-1", records=records,
                                        scope="中心院区", scope_id="site-a", confirmed=True)
    from openpyxl import load_workbook
    workbook = load_workbook(tmp_path / result["path"], read_only=True)
    assert workbook.active.max_row == 93
    assert workbook.active["H2"].value.endswith(records[0]["conclusion"])
    workbook.close()

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


def test_mcp_progress_preserves_partial_answers_and_tracks_explicit_completion_revision(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    payload = {"module": "general-ledger", "check_order": "GL-1-001", "research_id": "02",
               "config_item": "其他扩展应用", "conclusion": "本期不需要其他总账扩展应用",
               "scope": "测试院/本期", "confirmed_by": "test", "confirmed_at": "2026-01-01",
               "confirmed": True, "scope_id": "site-a", "research_complete": False}
    write_record(tmp_path, payload)
    path = tmp_path / ".dy_superpowers/modules/general-ledger/research.md"
    before = path.read_bytes()

    partial, error = server.execute_tool("dy_research_progress", {
        "trace_id": "partial-count", "module": "general-ledger"}, tmp_path)

    assert error is None
    assert (partial["confirmed_count"], partial["partial_count"], partial["answered"]) == (1, 1, 0)
    assert "1/82" in partial["progress_summary"]
    assert path.read_bytes() == before
    read, error = server.execute_tool("dy_module_memory_read", {
        "trace_id": "partial-count", "module": "general-ledger", "scope_id": "site-a"}, tmp_path)
    assert error is None
    record = read["records"][0]
    write_record(tmp_path, {**payload, "research_complete": True, "expected_revision": record["revision"]})

    complete, error = server.execute_tool("dy_research_progress", {
        "trace_id": "partial-count", "module": "general-ledger"}, tmp_path)

    assert error is None
    assert (complete["confirmed_count"], complete["partial_count"], complete["answered"]) == (1, 0, 1)
    assert complete["unanswered_count"] == 81


@pytest.mark.parametrize("module,total", [("expense", 92), ("general-ledger", 82)])
def test_mcp_progress_without_scope_routes_empty_memory_to_excel(tmp_path, monkeypatch, module, total):
    workspace = tmp_path / "project"
    workspace.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    result, error = server.execute_tool("dy_research_progress", {
        "trace_id": "progress-scope", "module": module}, workspace)
    assert error is None
    assert result["route"] == "excel"
    assert (result["answered"], result["total"], result["percentage"]) == (0, total, 0)
    assert (result["confirmed_count"], result["confirmed_percentage"], result["partial_count"],
            result["unknown_count"], result["unanswered_count"]) == (0, 0, 0, 0, total)
    assert result["scope"] is None and result["scope_id"] is None
    assert result["scope_mode"] == "unscoped"
    assert len(result["items"]) == total
    assert not (workspace / ".dy_superpowers").exists()


def _save_records(workspace, module, scope_id, start, stop, scope="中心院区"):
    for item in _guide_items(module)[start:stop]:
        write_record(workspace, {
            "module": module, "check_order": item["check_order"], "research_id": item["research_id"],
            "config_item": item["config_item"], "conclusion": "仅测试：已确认要素齐备",
            "scope": scope, "scope_id": scope_id, "confirmed_by": "test",
            "confirmed_at": "2026-01-01", "confirmed": True, "research_complete": True,
        })


@pytest.mark.parametrize("answered,route", [(57, "excel"), (58, "interactive")])
def test_mcp_progress_without_scope_reuses_single_scope(tmp_path, monkeypatch, answered, route):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    workspace = tmp_path / "project"
    _save_records(workspace, "general-ledger", "site-a", 0, answered)

    result, error = server.execute_tool("dy_research_progress", {
        "trace_id": "single-scope", "module": "general-ledger"}, workspace)

    assert error is None
    assert result["scope_id"] == "site-a" and result["scope"] == "中心院区"
    assert result["scope_mode"] == "selected"
    assert result["answered"] == answered and result["total"] == 82
    assert result["route"] == route


def test_mcp_unselected_scopes_report_separate_progress_and_export_full_template(tmp_path, monkeypatch):
    from openpyxl import load_workbook

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    workspace = tmp_path / "project"
    # 两个同名范围各45项；并集90项，不能冒充任一范围已达到70%。也覆盖跨页读取。
    _save_records(workspace, "expense", "site-a", 0, 45)
    _save_records(workspace, "expense", "site-b", 45, 90)
    # 补充重复键使总记录超过100，第三范围本身达到互动门槛也不能自动选中。
    _save_records(workspace, "expense", "site-c", 0, 65, scope="第三院区")
    memory_before = {path: path.read_bytes() for path in (workspace / ".dy_superpowers").rglob("*.md")}

    result, error = server.execute_tool("dy_research_progress", {
        "trace_id": "multiple-scopes", "module": "expense"}, workspace)
    assert error is None
    assert result["scope_mode"] == "multiple"
    assert result["scope"] is None and result["scope_id"] is None
    assert result["route"] == "excel" and result["answered"] == 0
    assert len(result["items"]) == 92
    assert [(row["scope_id"], row["answered"], row["route"]) for row in result["scope_progress"]] == [
        ("site-a", 45, "excel"), ("site-b", 45, "excel"), ("site-c", 65, "interactive")]

    exported, error = server.execute_tool("dy_research_unanswered_export", {
        "trace_id": "multiple-scopes", "module": "expense", "confirmed": True}, workspace)
    assert error is None
    assert exported["question_count"] == 92 and exported["scope_id"] is None
    with closing(load_workbook(workspace / exported["path"], read_only=True)) as workbook:
        rows = list(workbook.active.iter_rows(min_row=2, values_only=True))
        assert len(rows) == 92
        assert '中心院区' in rows[0][7] and '第三院区' in rows[0][7]
        assert all(row[9] is None for row in rows)
    assert all(path.read_bytes() == content for path, content in memory_before.items())

    selected, error = server.execute_tool("dy_research_progress", {
        "trace_id": "multiple-scopes", "module": "expense", "scope_id": "site-a"}, workspace)
    assert error is None and selected["answered"] == 45


@pytest.mark.parametrize("answered,question_count", [(0, 92), (1, 91)])
def test_mcp_export_without_scope_preserves_confirmation_and_reuses_single_scope(
        tmp_path, monkeypatch, answered, question_count):
    from openpyxl import load_workbook

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    workspace = tmp_path / "project"
    workspace.mkdir()
    _save_records(workspace, "expense", "site-a", 0, answered)
    arguments = {"trace_id": "export-no-scope", "module": "expense"}
    _, error = server.execute_tool("dy_research_unanswered_export", {
        **arguments, "confirmed": False}, workspace)
    assert error and "明确确认" in error["message"]
    assert not (workspace / ".dy_superpowers/research").exists()

    exported, error = server.execute_tool("dy_research_unanswered_export", {
        **arguments, "confirmed": True}, workspace)
    assert error is None
    assert exported["question_count"] == question_count
    assert exported["scope_id"] == ("site-a" if answered else None)
    with closing(load_workbook(workspace / exported["path"], read_only=True)) as workbook:
        rows = list(workbook.active.iter_rows(min_row=2, values_only=True))
        assert len(rows) == 92
        assert bool(rows[0][7]) == bool(answered)
        assert all(row[9] is None for row in rows)


def test_mcp_export_by_scope_id_keeps_known_scope_label(tmp_path, monkeypatch):
    from openpyxl import load_workbook

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    _save_records(tmp_path, "expense", "site-a", 0, 1)
    exported, error = server.execute_tool("dy_research_unanswered_export", {
        "trace_id": "export-scoped-label", "module": "expense", "scope_id": "site-a",
        "confirmed": True}, tmp_path)

    assert error is None
    with closing(load_workbook(tmp_path / exported["path"], read_only=True)) as workbook:
        assert '中心院区' in workbook.active["H2"].value


def test_mcp_export_by_new_scope_id_keeps_explicit_binding_without_inventing_label(tmp_path, monkeypatch):
    from openpyxl import load_workbook

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    exported, error = server.execute_tool("dy_research_unanswered_export", {
        "trace_id": "export-new-scope", "module": "expense", "scope_id": "site-new",
        "confirmed": True}, tmp_path)

    assert error is None
    assert exported["scope_id"] == "site-new" and exported["scope"] is None
    with closing(load_workbook(tmp_path / exported["path"], read_only=True)) as workbook:
        assert workbook.active["H2"].value is None


@pytest.mark.parametrize("tool", ["dy_research_progress", "dy_research_unanswered_export"])
def test_unscoped_routing_does_not_hide_corrupted_memory(tmp_path, monkeypatch, tool):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    _save_records(tmp_path, "expense", "site-a", 0, 1)
    path = tmp_path / ".dy_superpowers/modules/expense/research.md"
    path.write_text(path.read_text().replace("仅测试：已确认要素齐备", "未经确认的编辑"))
    arguments = {"trace_id": "corrupt-routing", "module": "expense"}
    if tool.endswith("export"):
        arguments["confirmed"] = True

    result, error = server.execute_tool(tool, arguments, tmp_path)

    assert result is None and error["code"] == -32602
    assert not (tmp_path / ".dy_superpowers/research").exists()


def test_multiple_scope_routing_preserves_unknown_completeness_and_unmapped_diagnostics():
    unknown = _records("expense", "site-a", 1)[0]
    del unknown["research_complete"]
    unmapped = {**_records("expense", "site-b", 1)[0], "check_order": "legacy"}

    result = calculate_routing_progress("expense", [unknown, unmapped])

    assert result["route"] == "excel" and result["answered"] == 0
    assert result["completeness_unknown"][0]["scope_id"] == "site-a"
    assert result["unmapped_records"][0]["scope_id"] == "site-b"
    assert all("conclusion" not in row and "history" not in row
               for field in ("completeness_unknown", "unmapped_records") for row in result[field])
