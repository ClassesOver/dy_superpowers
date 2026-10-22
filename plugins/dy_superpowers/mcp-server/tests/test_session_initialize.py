import json
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(PLUGIN_ROOT / "mcp-server/src"))

import server


def disable_telemetry(monkeypatch, codex_home: Path):
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(server, "initialize_telemetry", lambda: False)


def test_session_initialize_injects_memory_index_without_reading_memory_bodies(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "project"
    memory_dir = workspace / ".dy_superpowers"
    memory_dir.mkdir(parents=True)
    memory_content = {
        "preferences.md": "偏好标记" + "x" * 1500,
        "profile.md": "项目背景标记",
        "decisions.md": "决策标记",
        "delivery-register.md": "交付台账标记",
        "skill-candidates.md": "不应注入的候选标记",
        "module-research.md": "不应注入的调研正文标记",
    }
    for name, content in memory_content.items():
        (memory_dir / name).write_text(content, encoding="utf-8")
    disable_telemetry(monkeypatch, tmp_path / "codex")

    result, error = server.execute_tool(
        "dy_session_initialize",
        {"trace_id": "session-123", "source": "resume"},
        workspace,
    )

    assert error is None
    assert "按需读取" in result
    assert "dy_memory_read(trace_id, name)" in result
    assert "dy_memory_read" in result
    assert "preferences.md" in result
    assert "profile.md" in result
    assert "decisions.md" in result
    assert "delivery-register.md" in result
    assert "dy_module_memory_read" in result
    assert "偏好标记" not in result
    assert "项目背景标记" not in result
    assert "决策标记" not in result
    assert "交付台账标记" not in result
    assert "不应注入的调研正文标记" not in result

    trace_files = list((tmp_path / "codex/dy_superpowers/traces").rglob("*.jsonl"))
    assert len(trace_files) == 1
    trace = trace_files[0].read_text(encoding="utf-8")
    assert '"source_ref": "resume"' in trace
    assert '"operation": "session_start"' in trace
    assert "偏好标记" not in trace
    assert "项目背景标记" not in trace
    assert "module-research.md" not in trace


def test_session_initialize_with_no_memory_does_not_create_memory_directory(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "empty-project"
    disable_telemetry(monkeypatch, tmp_path / "codex")

    result, error = server.execute_tool(
        "dy_session_initialize",
        {"trace_id": "session-empty", "source": "startup"},
        workspace,
    )

    assert error is None
    assert "按需读取" in result
    assert "preferences.md" in result
    assert "dy_module_memory_read" in result
    assert not (workspace / ".dy_superpowers").exists()


def test_memory_read_returns_full_memory_without_truncation(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    memory_dir = workspace / ".dy_superpowers"
    memory_dir.mkdir(parents=True)
    content = "完整记忆内容" * 1500
    (memory_dir / "profile.md").write_text(content, encoding="utf-8")
    disable_telemetry(monkeypatch, tmp_path / "codex")

    result, error = server.execute_tool(
        "dy_memory_read",
        {"trace_id": "memory-long", "name": "profile.md"},
        workspace,
    )

    assert error is None
    assert result == content


def test_session_initialize_does_not_read_memory_files(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    memory_dir = workspace / ".dy_superpowers"
    memory_dir.mkdir(parents=True)
    (memory_dir / "preferences.md").write_text("仅供记忆读取", encoding="utf-8")
    disable_telemetry(monkeypatch, tmp_path / "codex")

    result, error = server.execute_tool(
        "dy_session_initialize",
        {"trace_id": "session-index", "source": "startup"},
        workspace,
    )

    assert error is None
    assert "仅供记忆读取" not in result
    trace_files = list((tmp_path / "codex/dy_superpowers/traces").rglob("*.jsonl"))
    trace = trace_files[0].read_text(encoding="utf-8")
    assert '"operation": "read"' not in trace


def test_session_initialize_rejects_unknown_source(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    disable_telemetry(monkeypatch, tmp_path / "codex")

    result, error = server.execute_tool(
        "dy_session_initialize",
        {"trace_id": "session-invalid", "source": "unknown"},
        workspace,
    )

    assert result is None
    assert error["code"] == -32602
    assert not (workspace / ".dy_superpowers").exists()


def test_plugin_session_start_hook_targets_registered_mcp_tool():
    manifest = json.loads(
        (PLUGIN_ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
    )
    mcp_config = json.loads((PLUGIN_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    hook_file = json.loads(
        (PLUGIN_ROOT / "hooks/hooks.json").read_text(encoding="utf-8")
    )

    assert manifest["hooks"] == "./hooks/hooks.json"
    assert list(mcp_config["mcpServers"]) == ["dy-superpowers-odoo"]
    hook_groups = hook_file["hooks"]["SessionStart"]
    assert len(hook_groups) == 1
    hook = hook_groups[0]["hooks"][0]
    assert hook["type"] == "mcp_tool"
    assert hook["server"] in mcp_config["mcpServers"]
    assert hook["tool"] == "dy_session_initialize"
    assert hook["tool"] in server.TOOLS
    assert hook["input"] == {
        "trace_id": "${session_id}",
        "source": "${source}",
    }


def test_mcp_tools_list_exposes_session_initialize_contract(tmp_path):
    response = server.handle({"id": 1, "method": "tools/list"}, tmp_path)

    tools = {tool["name"]: tool for tool in response["result"]["tools"]}
    initialize_tool = tools["dy_session_initialize"]
    assert initialize_tool["inputSchema"]["required"] == ["trace_id", "source"]
    assert initialize_tool["inputSchema"]["properties"]["source"]["enum"] == [
        "clear",
        "compact",
        "fork",
        "resume",
        "startup",
    ]
