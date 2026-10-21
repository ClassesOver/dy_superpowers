"""记忆读取错误码与调研记录损坏标记容错的边界测试。"""
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(PLUGIN_ROOT / "mcp-server/src"))

import server
from dy_superpowers_mcp.research import read_record, write_record


def disable_telemetry(monkeypatch, codex_home: Path):
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(server, "initialize_telemetry", lambda: False)


def research_arguments(**updates):
    arguments = {
        "module": "expense",
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
    disable_telemetry(monkeypatch, tmp_path / "codex")

    result, error = server.execute_tool(
        "dy_memory_read",
        {"trace_id": "memory-bad", "name": "../outside.md"},
        workspace,
    )
    assert result is None
    assert error["code"] == -32602
    assert "unsupported memory file" in error["message"]


def test_research_record_read_reports_workspace_escape_as_invalid_params(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / ".dy_superpowers").symlink_to(outside, target_is_directory=True)
    disable_telemetry(monkeypatch, tmp_path / "codex")

    result, error = server.execute_tool(
        "dy_research_record_read",
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

    first = read_record(workspace, "expense", "EXP-1-001", "01")
    second = read_record(workspace, "expense", "EXP-1-002", "01")
    assert first is not None
    assert second is not None

    write_record(workspace, research_arguments(conclusion="费用模块及OCR本期上线", expected_revision=first["revision"]))
    revised = read_record(workspace, "expense", "EXP-1-001", "01")
    assert revised["conclusion"] == "费用模块及OCR本期上线"
    assert read_record(workspace, "expense", "EXP-1-002", "01") is not None


def test_write_record_preserves_corrupted_data_marker_and_still_writes(tmp_path):
    workspace = tmp_path / "project"
    memory_dir = workspace / ".dy_superpowers"
    memory_dir.mkdir(parents=True)
    corrupted = "\n".join([
        "<!-- dy-research-record:start -->",
        "<!-- dy-research-record:data {bad json} -->",
        "<!-- dy-research-record:end -->",
    ])
    (memory_dir / "module-research.md").write_text(
        "# 模块调研结论\n\n" + corrupted + "\n", encoding="utf-8"
    )

    saved = write_record(workspace, research_arguments())

    assert saved["stored"] is True
    assert saved["revision_count"] == 0
    record = read_record(workspace, "expense", "EXP-1-001", "01")
    assert record["conclusion"] == "费用模块本期上线"
    text = (memory_dir / "module-research.md").read_text(encoding="utf-8")
    assert "{bad json}" in text
