"""记忆读取错误码与调研记录损坏标记容错的边界测试。"""
import sys
from pathlib import Path



PLUGIN_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(PLUGIN_ROOT / "mcp-server/src"))

import server


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
