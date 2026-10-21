import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from dy_superpowers_mcp.basic_memory import BasicMemoryAdapter


def test_adapter_builds_workspace_scoped_environment_and_search_command(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    adapter = BasicMemoryAdapter(workspace, command="basic-memory")

    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return type("Completed", (), {"returncode": 0, "stdout": json.dumps({"results": []}), "stderr": ""})()

    monkeypatch.setattr("dy_superpowers_mcp.basic_memory.subprocess.run", fake_run)

    result = adapter.search("交付台账")

    assert result["backend"] == "basic-memory"
    assert result["project"] == "dy-superpowers"
    assert result["results"] == []
    assert calls[0][0] == [
        "basic-memory",
        "project",
        "add",
        "dy-superpowers",
        str(workspace / ".dy_superpowers"),
        "--local",
        "--default",
    ]
    assert calls[1][0][:4] == ["basic-memory", "tool", "search-notes", "交付台账"]
    assert calls[1][0][calls[1][0].index("--project") + 1] == "dy-superpowers"
    assert calls[1][1]["env"]["BASIC_MEMORY_CONFIG_DIR"] == str(
        workspace / ".dy_superpowers-basic-memory"
    )


def test_basic_memory_runtime_directory_is_outside_indexed_memory(tmp_path):
    workspace = tmp_path / "project"
    adapter = BasicMemoryAdapter(workspace)

    assert adapter.config_dir == workspace / ".dy_superpowers-basic-memory"
    assert not adapter.config_dir.is_relative_to(adapter.memory_root)


def test_adapter_falls_back_to_workspace_markdown_when_basic_memory_unavailable(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    memory_dir = workspace / ".dy_superpowers"
    memory_dir.mkdir(parents=True)
    (memory_dir / "profile.md").write_text("项目使用 Odoo 12", encoding="utf-8")
    adapter = BasicMemoryAdapter(workspace)

    def unavailable(*args, **kwargs):
        raise FileNotFoundError("basic-memory")

    monkeypatch.setattr("dy_superpowers_mcp.basic_memory.subprocess.run", unavailable)

    result = adapter.search("Odoo 12")

    assert result["backend"] == "local-fallback"
    assert result["available"] is False
    assert result["results"] == [{"name": "profile.md", "matches": ["项目使用 Odoo 12"]}]


def test_adapter_rejects_workspace_escape_and_empty_query(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    adapter = BasicMemoryAdapter(workspace)

    with pytest.raises(ValueError, match="非空"):
        adapter.search(" ")

    escaped = BasicMemoryAdapter(workspace / "child")
    assert escaped.memory_root == (workspace / "child").resolve() / ".dy_superpowers"
