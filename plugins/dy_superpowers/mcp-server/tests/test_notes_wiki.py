"""wiki 记忆页（Basic Memory 兼容 md）读写工具回归。"""
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import server


def run(tool, args, ws):
    result, error = server.execute_tool(tool, {"trace_id": "notes-test", **args}, ws)
    return result, error


def test_notes_write_requires_confirmation_and_slug_rules(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(server, "initialize_telemetry", lambda: False)
    payload = {"slug": "expense-policy-overview", "title": "费用政策概览",
               "body": "# 费用政策概览\n住宿费政策头 1 个，详见 [[expense-run-202610]]。", "tags": ["expense", "policy"],
               "source_refs": ["费用模块调研结果.xlsx#费用调研清单!G53"]}
    _, error = run("dy_notes_write", {**payload, "confirmed": False}, tmp_path)
    assert error["code"] == -32602 and not (tmp_path / ".dy_superpowers/notes").exists()

    result, error = run("dy_notes_write", {**payload, "confirmed": True}, tmp_path)
    assert error is None and result["written"] is True
    page = tmp_path / ".dy_superpowers/notes/expense-policy-overview.md"
    text = page.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    metadata = yaml.safe_load(text.split("---", 2)[1])
    assert metadata["type"] == "note" and metadata["tags"] == ["expense", "policy"]
    assert "[[expense-run-202610]]" in text and "费用模块调研结果.xlsx" in text

    _, error = run("dy_notes_write", {**payload, "slug": "../escape", "confirmed": True}, tmp_path)
    assert error["code"] == -32602
    _, error = run("dy_notes_write", {**payload, "slug": "UPPER", "confirmed": True}, tmp_path)
    assert error["code"] == -32602
    assert not (tmp_path / ".dy_superpowers/notes/escape.md").exists()


def test_notes_read_missing_lists_available_and_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(server, "initialize_telemetry", lambda: False)
    missing, error = run("dy_notes_read", {"slug": "no-such-note"}, tmp_path)
    assert error is None and missing["found"] is False and missing["notes"] == []
    run("dy_notes_write", {"slug": "gl-scope", "title": "总账范围", "body": "范围说明", "confirmed": True}, tmp_path)
    listing, _ = run("dy_notes_list", {}, tmp_path)
    assert listing["notes"] == ["gl-scope"]
    content, _ = run("dy_notes_read", {"slug": "gl-scope"}, tmp_path)
    assert isinstance(content, str) and "总账范围" in content and content.startswith("---\n")
    assert missing  # noqa: B018 - 保持可读性
