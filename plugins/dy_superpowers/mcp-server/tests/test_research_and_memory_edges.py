"""记忆读取错误码与调研记录损坏标记容错的边界测试。"""
import sys
from pathlib import Path

import pytest


PLUGIN_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(PLUGIN_ROOT / "mcp-server/src"))

import server
from dy_superpowers_mcp.research import ResearchIntegrityError, read_module_records, read_record, write_record


def isolate_codex_home(monkeypatch, codex_home: Path):
    monkeypatch.setenv("CODEX_HOME", str(codex_home))


def research_arguments(**updates):
    arguments = {
        "module": "test-expense",
        "check_order": "EXP-1-001",
        "research_id": "01",
        "config_item": "应用安装",
        "conclusion": "费用模块本期上线",
        "scope": "公司A/本期",
        "evidence_ref": "需求文档#4",
        "confirmed_by": "顾问",
        "confirmed_at": "2026-10-21",
        "confirmed": True,
    }
    arguments.update(updates)
    return arguments


def test_memory_read_unsupported_name_returns_invalid_params(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    isolate_codex_home(monkeypatch, tmp_path / "codex")

    result, error = server.execute_tool(
        "dy_memory_read",
        {"trace_id": "memory-bad", "name": "../outside.md"},
        workspace,
    )
    assert result is None
    assert error["code"] == -32602


def test_research_record_read_reports_workspace_escape_as_invalid_params(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / ".dy_superpowers").symlink_to(outside, target_is_directory=True)
    isolate_codex_home(monkeypatch, tmp_path / "codex")

    result, error = server.execute_tool(
        "dy_module_memory_read",
        {"trace_id": "research-escape", "module": "expense", "check_order": "EXP-1-001", "research_id": "01"},
        workspace,
    )
    assert result is None
    assert error["code"] == -32602


def test_write_and_read_supports_multiple_check_keys(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()

    write_record(workspace, research_arguments())
    write_record(workspace, research_arguments(check_order="EXP-1-002"))

    first = read_record(workspace, "test-expense", "EXP-1-001", "01")
    second = read_record(workspace, "test-expense", "EXP-1-002", "01")
    assert first is not None
    assert second is not None

    write_record(workspace, research_arguments(conclusion="费用模块及OCR本期上线", expected_revision=first["revision"]))
    revised = read_record(workspace, "test-expense", "EXP-1-001", "01")
    assert revised["conclusion"] == "费用模块及OCR本期上线"
    assert read_record(workspace, "test-expense", "EXP-1-002", "01") is not None


def test_write_record_preserves_corrupted_data_marker_and_still_writes(tmp_path):
    workspace = tmp_path / "project"
    memory_dir = workspace / ".dy_superpowers"
    (memory_dir / "modules" / "test-expense").mkdir(parents=True)
    corrupted = "\n".join([
        "<!-- dy-research-record:start -->",
        "<!-- dy-research-record:data {bad json} -->",
        "<!-- dy-research-record:end -->",
    ])
    (memory_dir / "modules" / "test-expense" / "research.md").write_text(
        "# 模块调研结论\n\n" + corrupted + "\n", encoding="utf-8"
    )

    saved = write_record(workspace, research_arguments())

    assert saved["stored"] is True
    assert saved["revision_count"] == 0
    record = read_record(workspace, "test-expense", "EXP-1-001", "01")
    assert record["conclusion"] == "费用模块本期上线"
    text = (memory_dir / "modules" / "test-expense" / "research.md").read_text(encoding="utf-8")
    assert "{bad json}" in text


def test_read_record_with_scope_and_scope_id_reports_integrity_error_on_corrupted_block(tmp_path):
    workspace = tmp_path / "project"
    module_dir = workspace / ".dy_superpowers" / "modules" / "test-expense"
    module_dir.mkdir(parents=True)
    # 标题可识别但数据不可解码的损坏块：双参读取必须抛完整性错误，而不是崩溃或误报缺失。
    corrupted = "\n".join([
        "<!-- dy-research-record:start -->",
        "## test-expense / 应用安装（EXP-1-001 + 01）",
        "- 调研结论：损坏记录",
        "<!-- dy-research-record:data {bad json} -->",
        "<!-- dy-research-record:end -->",
    ])
    (module_dir / "research.md").write_text("# 模块调研结论\n\n" + corrupted + "\n", encoding="utf-8")

    with pytest.raises(ResearchIntegrityError):
        read_record(workspace, "test-expense", "EXP-1-001", "01", scope="公司A/本期", scope_id="scope-1")


def test_read_record_normalizes_scope_when_scope_id_is_also_supplied(tmp_path):
    workspace = tmp_path / "project"
    write_record(workspace, research_arguments(scope="公司A / 本期"))
    saved = read_record(workspace, "test-expense", "EXP-1-001", "01")

    record = read_record(workspace, "test-expense", "EXP-1-001", "01",
                          scope="公司A/本期", scope_id=saved["scope_id"])

    assert record["conclusion"] == "费用模块本期上线"


def test_read_module_records_normalizes_scope_when_scope_id_is_also_supplied(tmp_path):
    workspace = tmp_path / "project"
    write_record(workspace, research_arguments(scope="公司A / 本期"))
    saved = read_record(workspace, "test-expense", "EXP-1-001", "01")

    records, more = read_module_records(workspace, "test-expense", scope="公司A/本期",
                                        scope_id=saved["scope_id"])

    assert not more
    assert len(records) == 1 and records[0]["conclusion"] == "费用模块本期上线"
