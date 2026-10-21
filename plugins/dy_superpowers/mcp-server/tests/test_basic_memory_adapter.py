import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from dy_superpowers_mcp.basic_memory import BasicMemoryAdapter
import server


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
    assert calls[2][0][:4] == ["basic-memory", "tool", "search-notes", "交付台账"]
    assert calls[2][0][calls[2][0].index("--project") + 1] == "dy-superpowers"
    assert calls[2][1]["env"]["BASIC_MEMORY_CONFIG_DIR"] == str(
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


def test_adapter_rejects_empty_query_and_pins_memory_root_to_workspace(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    adapter = BasicMemoryAdapter(workspace)

    with pytest.raises(ValueError, match="非空"):
        adapter.search(" ")

    scoped = BasicMemoryAdapter(workspace / "child")
    assert scoped.memory_root == (workspace / "child").resolve() / ".dy_superpowers"


def test_adapter_rejects_leading_dash_query_without_spawning_subprocess(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    adapter = BasicMemoryAdapter(workspace)

    def must_not_run(*args, **kwargs):
        raise AssertionError("不应触达 basic-memory CLI")

    monkeypatch.setattr("dy_superpowers_mcp.basic_memory.subprocess.run", must_not_run)

    with pytest.raises(ValueError, match="连字符"):
        adapter.search("--project=other")


def test_memory_search_dispatch_rejects_leading_dash_before_building_adapter(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(server, "initialize_telemetry", lambda: False)

    def must_not_construct(workspace_arg):
        raise AssertionError("参数校验应先于适配器构造")

    monkeypatch.setattr(server, "BasicMemoryAdapter", must_not_construct)

    result, error = server.execute_tool(
        "dy_memory_search",
        {"trace_id": "memory-dash", "query": "--local"},
        workspace,
    )
    assert result is None
    assert error["code"] == -32602

@pytest.mark.parametrize('target', ['directory', 'file', 'config'])
def test_search_rejects_symlink_escape_before_cli(tmp_path, monkeypatch, target):
    workspace = tmp_path / 'project'
    workspace.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'profile.md').write_text('外部秘密', encoding='utf-8')
    memory = workspace / '.dy_superpowers'
    if target == 'directory':
        memory.symlink_to(outside, target_is_directory=True)
    elif target == 'config':
        (workspace / '.dy_superpowers-basic-memory').symlink_to(outside, target_is_directory=True)
    else:
        memory.mkdir()
        (memory / 'profile.md').symlink_to(outside / 'profile.md')

    def must_not_run(*args, **kwargs):
        pytest.fail('越界路径不应触达 CLI')

    monkeypatch.setattr('dy_superpowers_mcp.basic_memory.subprocess.run', must_not_run)
    with pytest.raises(ValueError):
        BasicMemoryAdapter(workspace).search('秘密')


def test_search_falls_back_on_unexpected_json_structure(tmp_path, monkeypatch):
    memory = tmp_path / '.dy_superpowers'
    memory.mkdir()
    (memory / 'profile.md').write_text('项目背景', encoding='utf-8')
    adapter = BasicMemoryAdapter(tmp_path)
    monkeypatch.setattr(adapter, '_ensure_project', lambda: None)
    monkeypatch.setattr(adapter, '_run', lambda args: '{"error":"search failed"}')
    result = adapter.search('项目')
    assert result['backend'] == 'local-fallback'
    assert result['results'][0]['name'] == 'profile.md'


def test_search_refreshes_index_before_query_and_uses_text_search(tmp_path, monkeypatch):
    adapter = BasicMemoryAdapter(tmp_path)
    calls = []
    monkeypatch.setattr(adapter, '_ensure_project', lambda: None)

    def run(args):
        calls.append(args)
        return '{"results":[]}'

    monkeypatch.setattr(adapter, '_run', run)
    result = adapter.search('项目')
    assert result['available'] is True
    assert calls[0] == ['reindex', '--search', '--project', 'dy-superpowers']
    assert calls[1][:3] == ['tool', 'search-notes', '项目']
    assert adapter._environment()['BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED'] == 'false'
    assert adapter._environment()['BASIC_MEMORY_DEFAULT_SEARCH_TYPE'] == 'text'


def test_index_timeout_returns_current_markdown_instead_of_stale_index(tmp_path, monkeypatch):
    import subprocess

    memory = tmp_path / '.dy_superpowers'
    memory.mkdir()
    (memory / 'profile.md').write_text('当前结论', encoding='utf-8')
    adapter = BasicMemoryAdapter(tmp_path)
    monkeypatch.setattr(adapter, '_ensure_project', lambda: None)

    def timeout(args):
        assert args[0] == 'reindex'
        raise subprocess.TimeoutExpired(args, 20)

    monkeypatch.setattr(adapter, '_run', timeout)
    result = adapter.search('当前')
    assert result['available'] is False
    assert result['results'] == [{'name': 'profile.md', 'matches': ['当前结论']}]


def test_fallback_reports_omitted_matches_and_full_read_route(tmp_path, monkeypatch):
    memory = tmp_path / '.dy_superpowers'
    memory.mkdir()
    (memory / 'profile.md').write_text('\n'.join(f'项目记录{i}' for i in range(25)), encoding='utf-8')
    adapter = BasicMemoryAdapter(tmp_path)
    monkeypatch.setattr(adapter, '_ensure_project', lambda: (_ for _ in ()).throw(FileNotFoundError()))
    result = adapter.search('项目')
    assert result['has_more'] is True
    assert result['total_matches'] == 25
    assert result['read_full_files'] == ['profile.md']
    assert len(result['results'][0]['matches']) == 20


def test_full_backend_page_is_marked_as_potentially_incomplete(tmp_path, monkeypatch):
    adapter = BasicMemoryAdapter(tmp_path)
    monkeypatch.setattr(adapter, '_ensure_project', lambda: None)
    monkeypatch.setattr(adapter, '_run', lambda args: json.dumps({'results': [{}] * 20}))
    result = adapter.search('项目')
    assert result['has_more'] is True
    assert 'dy_memory_read' in result['completeness_note']
