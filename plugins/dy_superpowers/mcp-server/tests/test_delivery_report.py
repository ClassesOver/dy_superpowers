import sys
import io
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import server
from dy_superpowers_mcp.inspection import _matrix, validate_inspection
from dy_superpowers_mcp.reports import export_report_pdf, save_report
from dy_superpowers_mcp import reports
from dy_superpowers_mcp.report_pdf import render_report_pdf


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


def test_report_save_produces_markdown_and_fixed_structure_pdf(tmp_path, monkeypatch):
    pypdf = pytest.importorskip("pypdf")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(server, "initialize_telemetry", lambda: False)
    result, error = server.execute_tool("dy_inspection_report_save", {
        "trace_id": "report-pdf", "confirmed": True, "report": valid_report(),
    }, tmp_path)
    assert error is None
    md_path = tmp_path / result["path"]
    pdf_path = tmp_path / result["pdf_path"]
    assert md_path.suffix == ".md" and pdf_path == md_path.with_suffix(".pdf")
    assert md_path.is_file() and pdf_path.is_file()
    raw = pdf_path.read_bytes()
    assert raw[:5] == b"%PDF-"
    import io

    text = "\n".join(page.extract_text() for page in pypdf.PdfReader(io.BytesIO(raw)).pages)
    headings = ["一、一页结论", "二、范围概览", "三、检查结果汇总", "四、阻塞事项与下一步", "五、技术追溯"]
    positions = [text.find(heading) for heading in headings]
    assert all(position >= 0 for position in positions), "固定章节缺失"
    assert positions == sorted(positions), "固定章节顺序被打乱"
    assert result["report_id"] in text and "交付结论" in text
    assert "完整检查数据" not in text and '"expected_checks"' not in text, "机器可读数据不得进入业务 PDF"
    assert "第 1 页" in text, "PDF 应带页码页脚"


def test_report_export_pdf_from_saved_markdown(tmp_path, monkeypatch):
    pytest.importorskip("pypdf")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(server, "initialize_telemetry", lambda: False)
    saved, _ = server.execute_tool("dy_inspection_report_save", {
        "trace_id": "report-export", "confirmed": True, "report": valid_report(),
    }, tmp_path)
    assert _ is None
    (tmp_path / saved["pdf_path"]).unlink()  # 删除保存时生成的 PDF，模拟旧版无 PDF 的历史报告

    result, error = server.execute_tool("dy_inspection_report_export_pdf", {
        "trace_id": "report-export-2", "module": "expense", "report_id": saved["report_id"],
    }, tmp_path)
    assert error is None
    assert result["created"] is True
    pdf_path = tmp_path / result["pdf_path"]
    assert pdf_path.is_file() and pdf_path.read_bytes()[:5] == b"%PDF-"

    again, error = server.execute_tool("dy_inspection_report_export_pdf", {
        "trace_id": "report-export-3", "module": "expense", "report_id": saved["report_id"],
    }, tmp_path)
    assert error is None and again["created"] is False  # 已有 PDF 不覆盖

    broken = tmp_path / ".dy_superpowers/reports/expense/broken.md"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text("# 无 JSON 块的报告\n", encoding="utf-8")
    _, error = server.execute_tool("dy_inspection_report_export_pdf", {
        "trace_id": "report-export-4", "module": "expense", "report_id": "broken",
    }, tmp_path)
    assert error["code"] == -32602 and "机器可读" in error["message"]


def test_report_reexport_preserves_saved_validation_and_generation_time(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    report = valid_report()
    report["_generated_at"] = "1999-01-01T00:00:00Z"
    validation = validate_inspection(tmp_path, report)
    validation["validated_at"] = "2020-01-02T03:04:05Z"
    report_id, md_path, pdf_path = save_report(tmp_path, "expense", report, validation)
    saved_md = md_path.read_text(encoding="utf-8")
    pdf_path.unlink()

    exported, created = export_report_pdf(tmp_path, "expense", report_id)

    assert created is True
    text = "\n".join(page.extract_text() for page in pypdf.PdfReader(io.BytesIO(exported.read_bytes())).pages)
    assert text.count("2020-01-02T03:04:05Z") == 2, "生成时间和技术追溯必须保留原始校验时间"
    assert "1999-01-01T00:00:00Z" not in text
    assert md_path.read_text(encoding="utf-8") == saved_md


def test_report_save_removes_markdown_when_pdf_write_fails(tmp_path, monkeypatch):
    report = valid_report()
    validation = validate_inspection(tmp_path, report)

    def fail_pdf_write(path, content):
        raise OSError("simulated PDF write failure")

    monkeypatch.setattr(reports, "_write_new_bytes", fail_pdf_write)
    with pytest.raises(OSError, match="simulated PDF"):
        save_report(tmp_path, "expense", report, validation)

    report_dir = tmp_path / ".dy_superpowers/reports/expense"
    assert not list(report_dir.glob("*.md"))
    assert not list(report_dir.glob("*.pdf"))


def test_pdf_uses_same_business_label_as_markdown(monkeypatch):
    captured = {}

    class FakeHTML:
        def __init__(self, *, string):
            captured["html"] = string

        def write_pdf(self):
            return b"%PDF-test"

    monkeypatch.setitem(sys.modules, "weasyprint", type("FakeWeasyPrint", (), {"HTML": FakeHTML}))
    report = {"expected_checks": [], "excluded_checks": [], "records": [{
        "check_order": "GL-8-026-02", "research_id": "01", "result": "未知",
        "reason": "待核验",
    }]}
    validation = {"gate": "待确认", "coverage": {}, "validated_at": "2026-10-28T00:00:00Z"}

    render_report_pdf("general-ledger", "report-1", report, validation)

    assert "GL-026-02-01" in captured["html"]
    assert "GL-8-026-02 + 01" not in captured["html"]


@pytest.mark.parametrize("replacement", [
    "",
    "- 校验时间：2020-01-02T03:04:05Z\n- 校验时间：2020-01-02T03:04:05Z",
    "- 校验时间：不是时间",
    "- 校验时间：2020-01-02",
    "- 校验时间：2020-01-02T03:04:05",
])
def test_report_reexport_rejects_missing_duplicate_or_invalid_saved_time(tmp_path, replacement):
    report = valid_report()
    # 字段内容里的相似文字不是独立的技术时间行，不能用于猜测或补全。
    report["records"][0]["reason"] = "待核验\n- 校验时间：2020-01-02T03:04:05Z"
    validation = validate_inspection(tmp_path, report)
    report_id, md_path, pdf_path = save_report(tmp_path, "expense", report, validation)
    content = md_path.read_text(encoding="utf-8")
    original = f"- 校验时间：{validation['validated_at']}"
    md_path.write_text(content.replace(original, replacement), encoding="utf-8")
    pdf_path.unlink()

    with pytest.raises(ValueError, match="校验时间"):
        export_report_pdf(tmp_path, "expense", report_id)
    assert not pdf_path.exists()


def test_report_reexport_still_rejects_outdated_matrix(tmp_path):
    report = valid_report()
    validation = validate_inspection(tmp_path, report)
    report["records"][0]["matrix_revision"] = "旧版矩阵"
    report_id, _, pdf_path = save_report(tmp_path, "expense", report, validation)
    pdf_path.unlink()

    with pytest.raises(ValueError, match="当前矩阵重新校验未通过"):
        export_report_pdf(tmp_path, "expense", report_id)
    assert not pdf_path.exists()
