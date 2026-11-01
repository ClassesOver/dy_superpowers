import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from dy_superpowers_mcp.basic_memory import BasicMemoryAdapter
import server
from dy_superpowers_mcp import basic_memory


def test_basic_memory_required_version_is_installed():
    from importlib.metadata import version

    assert version("basic-memory") == "0.23.2"


def test_default_adapter_uses_cli_from_its_python_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("DY_BASIC_MEMORY_COMMAND", raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "venv/bin/python"))
    assert BasicMemoryAdapter(tmp_path).command == str(tmp_path / "venv/bin/basic-memory")


def test_required_backend_indexes_and_searches_workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("DY_BASIC_MEMORY_COMMAND", raising=False)
    memory = tmp_path / ".dy_superpowers"
    memory.mkdir()
    (memory / "profile.md").write_text("# 项目背景\n\nmandatorybackendprobe\n", encoding="utf-8")

    result = BasicMemoryAdapter(tmp_path).search("mandatorybackendprobe")

    assert result["backend"] == "basic-memory", result
    assert result["available"] is True
    assert result["results"], result
    assert (tmp_path / ".dy_superpowers-basic-memory/config.json").is_file()


@pytest.mark.parametrize('original', [
    '# 项目背景\n\nimmutablememoryprobe\n',
    '---\ntitle: 已有项目背景\ntype: note\n---\n\nimmutablememoryprobe\n',
])
def test_search_does_not_rewrite_authoritative_markdown(tmp_path, monkeypatch, original):
    monkeypatch.delenv('DY_BASIC_MEMORY_COMMAND', raising=False)
    memory = tmp_path / '.dy_superpowers'
    memory.mkdir()
    path = memory / 'profile.md'
    path.write_text(original, encoding='utf-8')
    result = BasicMemoryAdapter(tmp_path).search('immutablememoryprobe')
    assert result['backend'] == 'basic-memory' and result['results']
    assert path.read_bytes() == original.encode('utf-8')


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


def test_module_search_scans_later_basic_memory_pages(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    adapter = BasicMemoryAdapter(workspace, command="basic-memory")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[1:3] == ["tool", "search-notes"]:
            page = int(command[command.index("--page") + 1])
            rows = ([{"path": "modules/account/research.md", "title": "其他模块"}]
                    if page == 1 else [{"path": "modules/expense/research.md", "title": "目标模块"}])
            return type("Completed", (), {"returncode": 0, "stdout": json.dumps({
                "results": rows, "has_more": page == 1,
            }), "stderr": ""})()
        return type("Completed", (), {"returncode": 0, "stdout": "{}", "stderr": ""})()

    monkeypatch.setattr("dy_superpowers_mcp.basic_memory.subprocess.run", fake_run)
    result = adapter.search("配置", module="expense")
    assert [row["title"] for row in result["results"]] == ["目标模块"]
    assert [int(call[call.index("--page") + 1]) for call in calls if "--page" in call] == [1, 2]


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


def test_module_scoped_fallback_search_does_not_mix_project_memory(tmp_path, monkeypatch):
    memory = tmp_path / '.dy_superpowers'
    module_dir = memory / 'modules' / 'expense'
    module_dir.mkdir(parents=True)
    (memory / 'profile.md').write_text('费用项目背景配置', encoding='utf-8')
    (module_dir / 'research.md').write_text('费用模块配置结论', encoding='utf-8')
    adapter = BasicMemoryAdapter(tmp_path)
    monkeypatch.setattr(adapter, '_ensure_project', lambda: (_ for _ in ()).throw(FileNotFoundError()))
    result = adapter.search('费用', module='expense')
    assert result['backend'] == 'local-fallback'
    assert [row['name'] for row in result['results']] == ['modules/expense/research.md']


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

    def must_not_construct(workspace_arg):
        raise AssertionError("参数校验应先于适配器构造")

    monkeypatch.setattr(basic_memory, 'BasicMemoryAdapter', must_not_construct)

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
