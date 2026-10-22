import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import server
from dy_superpowers_mcp.inspection import _matrix


def valid_report():
    revision, standards = _matrix("expense")
    check_order, research_id = sorted(standards)[0]
    scope = {"module": "expense", "check_order": check_order, "research_id": research_id,
             "scope_id": "scope-a", "environment": "UAT"}
    expected = [scope]
    excluded = [
        {"module": "expense", "check_order": order, "research_id": item_id,
         "scope_id": "scope-a", "environment": "UAT", "reason": "本次不在交付范围"}
        for order, item_id in sorted(standards) if (order, item_id) != (check_order, research_id)
    ]
    record = {
        **scope, "record_id": "record-a", "research_revision": "",
        "matrix_revision": revision, "acceptance_standard": standards[check_order, research_id],
        "verification_method": "待确认", "verified_at": datetime.now(timezone.utc).isoformat(),
        "evidence_refs": [], "result": "未知", "reason": "待现场核验",
    }
    return {"expected_checks": expected, "excluded_checks": excluded, "records": [record]}


def test_report_save_requires_confirmation_and_revalidates_before_fixed_path_write(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(server, "initialize_telemetry", lambda: False)
    result, error = server.execute_tool("dy_inspection_report_save", {
        "trace_id": "report-save", "confirmed": False,
        "report": {"expected_checks": [], "records": []},
    }, tmp_path)
    assert result is None and error["code"] == -32602
    assert not (tmp_path / ".dy_superpowers/reports").exists()


def test_report_save_writes_validated_report_under_module_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(server, "initialize_telemetry", lambda: False)
    report = valid_report()
    result, error = server.execute_tool("dy_inspection_report_save", {
        "trace_id": "report-invalid", "confirmed": True,
        "report": report,
    }, tmp_path)
    assert error is None
    report_id = result["report_id"]
    assert report_id.startswith(datetime.now(timezone.utc).strftime("%Y%m%dT"))
    report_date = report_id[:4] + "-" + report_id[4:6] + "-" + report_id[6:8]
    path = tmp_path / ".dy_superpowers/reports/expense" / report_date / f"{report_id}.md"
    assert result["path"] == str(path.relative_to(tmp_path))
    assert path.is_file()
    saved_text = path.read_text()
    assert "# 费用模块巡检报告" in saved_text
    assert "## 一页结论" in saved_text
    assert "## 阻塞事项与下一步" in saved_text
    assert "## 巡检明细（按业务分类）" in saved_text
    assert "## 技术追溯" in saved_text
    assert "## 完整检查数据（机器可读）" in saved_text
    assert "## 巡检结果" not in saved_text
    assert "### 一、基础配置" in saved_text
    assert "### 06｜费用需占用专项预算" not in saved_text
    loaded, error = server.execute_tool("dy_inspection_report_read", {
        "trace_id": "report-read", "module": "expense", "report_id": report_id,
    }, tmp_path)
    assert error is None
    assert "待现场核验" in loaded
    assert "## 完整检查数据（机器可读）" in loaded
    _, conflict = server.execute_tool("dy_inspection_report_save", {
        "trace_id": "report-conflict",
        "confirmed": True, "report": valid_report(),
    }, tmp_path)
    assert conflict is None
    assert result["report_id"] != _["report_id"]
    assert path.read_text() == saved_text


def test_report_save_schema_does_not_require_caller_generated_id():
    schema = server.TOOL_SCHEMAS["dy_inspection_report_save"]
    assert "report_id" not in schema["required"]


def test_report_read_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(server, "initialize_telemetry", lambda: False)
    _, error = server.execute_tool("dy_inspection_report_read", {
        "trace_id": "report-path", "module": "expense", "report_id": "../outside",
    }, tmp_path)
    assert error["code"] == -32602


def test_report_text_cannot_inject_markdown_sections_or_close_machine_data_fence(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(server, "initialize_telemetry", lambda: False)
    report = valid_report()
    report["evidence_summary"] = {"summary": "现状已记录\n## 伪造交付结论"}
    report["next_steps"] = ["继续核对\n## 伪造下一章节\n```\n# 错误声明"]
    report["records"][0]["verification_method"] = "人工核对\n### 伪造验证方法"
    report["records"][0]["reason"] = "仍待确认\n### 伪造通过状态\n```"

    result, error = server.execute_tool("dy_inspection_report_save", {
        "trace_id": "report-markdown-injection", "confirmed": True, "report": report,
    }, tmp_path)

    assert error is None
    saved_text = (tmp_path / result["path"]).read_text(encoding="utf-8")
    prose, machine_data = saved_text.split("## 完整检查数据（机器可读）", 1)
    assert "\n## 伪造交付结论" not in prose
    assert "\n## 伪造下一章节" not in prose
    assert "\n### 伪造验证方法" not in prose
    assert "\n### 伪造通过状态" not in prose
    assert "\n# 错误声明" not in prose
    assert "````json\n" in machine_data
    assert machine_data.count("````") == 2
