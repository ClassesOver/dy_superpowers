"""完整性审查发现的运行边界回归测试。"""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

import server
from dy_superpowers_mcp.basic_memory import BasicMemoryAdapter
from dy_superpowers_mcp.memory import WorkspaceMemory, module_memory_path
from dy_superpowers_mcp.knowledge import module_info
from dy_superpowers_mcp.odoo_jsonrpc import OdooReadClient, OdooSettings
from dy_superpowers_mcp.research import read_record, write_record, _legacy_visible
from dy_superpowers_mcp.trace import TraceStore, otel_event_attributes, sanitize_trace_event


def research_data(**updates):
    data = dict(module="account", check_order="1", research_id="01",
                config_item="会计期间", conclusion="按月关闭", scope="公司A",
                confirmed_by="顾问", confirmed_at="2026-10-21", confirmed=True)
    return {**data, **updates}


def test_module_query_uses_odoo12_dependency_relation(monkeypatch):
    monkeypatch.setenv("DY_ODOO_ALLOWED_ORIGINS", "http://odoo")
    calls = []

    class Response:
        def __init__(self, result):
            self.result = result

        def raise_for_status(self):
            pass

        def json(self):
            return {"jsonrpc": "2.0", "id": calls[-1]["json"]["id"], "result": self.result}

    class Session:
        trust_env = True

        def post(self, url, **kwargs):
            calls.append({"url": url, **kwargs})
            params = kwargs["json"]["params"]
            if params["service"] == "common":
                return Response(1)
            model_kwargs = params["args"][6]
            # 标准 Odoo 12 模型字段；拒绝 manifest 中才存在的 depends。
            allowed = {"name", "shortdesc", "latest_version", "state", "dependencies_id"}
            assert set(model_kwargs["fields"]) <= allowed
            return Response([{"name": "account", "dependencies_id": [7]}])

        def close(self):
            pass

    session = Session()
    client = OdooReadClient(OdooSettings("http://odoo", "db", "u", "p"),
                            session_factory=lambda: session)
    assert client.module_records()[0]["dependencies_id"] == [7]
    assert len(calls) == 2
    assert all(call["url"] == "http://odoo/jsonrpc" for call in calls)
    assert calls[0]["json"]["params"] == {
        "service": "common", "method": "login", "args": ["db", "u", "p"],
    }
    assert calls[1]["json"]["params"]["args"][:3] == ["db", 1, "p"]


def test_account_knowledge_distinguishes_posting_from_payment():
    info = module_info("account")
    assert info["odoo_version"] == "12.0"
    assert info["workflow"] == "会计分录：草稿 → 已过账；付款与核销需单独验证"
    assert "自定义" in info["limitations"]


@pytest.mark.parametrize("url", ["http://odoo", "https://odoo"])
def test_odoo_jsonrpc_has_finite_timeout_and_does_not_follow_redirects(url, monkeypatch):
    monkeypatch.setenv("DY_ODOO_ALLOWED_ORIGINS", url)
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"jsonrpc": "2.0", "id": calls[-1]["json"]["id"], "result": 1}

    class Session:
        trust_env = True

        def post(self, endpoint, **kwargs):
            calls.append({"endpoint": endpoint, **kwargs})
            return Response()

        def close(self):
            pass

    session = Session()
    client = OdooReadClient(OdooSettings(url, "db", "u", "p"),
                            session_factory=lambda: session)
    assert session.trust_env is False
    assert calls[0]["endpoint"] == f"{url}/jsonrpc"
    assert calls[0]["timeout"] == 20
    assert calls[0]["allow_redirects"] is False
    client.close()


@pytest.mark.parametrize("config", [[], {"projects": []}, {"projects": None},
                                    {"projects": {"dy-superpowers": {"path": None}}}])
def test_malformed_basic_memory_config_falls_back(tmp_path, monkeypatch, config):
    WorkspaceMemory(tmp_path).write("profile.md", "当前项目", confirmed=True)
    state = tmp_path / ".dy_superpowers-basic-memory"
    state.mkdir()
    (state / "config.json").write_text(json.dumps(config))
    adapter = BasicMemoryAdapter(tmp_path)
    monkeypatch.setattr(adapter, "_run", lambda args: pytest.fail("损坏配置应直接回退"))
    assert adapter.search("当前")["backend"] == "local-fallback"


def test_trace_preserves_operation_metadata_without_body():
    event = {"model": "account.move", "module": "account", "count": 3,
             "research_id": "01", "content": "业务正文"}
    assert sanitize_trace_event(event) == {key: value for key, value in event.items() if key != "content"}
    assert otel_event_attributes(event) == {
        "dy.model": "account.move", "dy.module": "account", "dy.count": 3, "dy.research_id": "01",
    }


def test_manual_visible_edit_is_rejected_before_research_use(tmp_path):
    write_record(tmp_path, research_data())
    path = module_memory_path(tmp_path, "account")
    current = read_record(tmp_path, "account", "1", "01")
    current.pop("revision")
    path.write_text("\n".join(_legacy_visible(current)))
    path.write_text(path.read_text().replace("- 调研结论：按月关闭", "- 调研结论：按年关闭"))
    with pytest.raises(ValueError, match="不一致"):
        read_record(tmp_path, "account", "1", "01")
    with pytest.raises(ValueError, match="不一致"):
        write_record(tmp_path, research_data(conclusion="新结论"))


def test_research_keeps_independent_current_scope_and_history(tmp_path):
    write_record(tmp_path, research_data())
    write_record(tmp_path, research_data(scope="公司B", conclusion="按季关闭"))
    write_record(tmp_path, research_data(conclusion="按月复核关闭", expected_revision=read_record(tmp_path, "account", "1", "01", scope="公司A")["revision"]))
    with pytest.raises(ValueError, match="适用范围"):
        read_record(tmp_path, "account", "1", "01")
    first = read_record(tmp_path, "account", "1", "01", scope="公司A")
    second = read_record(tmp_path, "account", "1", "01", scope="公司B")
    assert first["conclusion"] == "按月复核关闭"
    assert first["history"][0]["conclusion"] == "按月关闭"
    assert second["conclusion"] == "按季关闭"
    assert second["history"] == []


def test_memory_update_requires_original_content(tmp_path):
    memory = WorkspaceMemory(tmp_path)
    memory.write("delivery-register.md", "原待办", confirmed=True)
    with pytest.raises(ValueError, match="expected_content"):
        memory.write("delivery-register.md", "新待办", confirmed=True)
    memory.write("delivery-register.md", "新待办", confirmed=True, expected_content="原待办")
    with pytest.raises(ValueError, match="冲突"):
        memory.write("delivery-register.md", "过期待办", confirmed=True, expected_content="原待办")
    assert memory.read("delivery-register.md") == "新待办"


def test_concurrent_memory_updates_cannot_lose_a_committed_update(tmp_path):
    memory = WorkspaceMemory(tmp_path)
    memory.write("delivery-register.md", "原待办", confirmed=True)

    def update(index):
        try:
            memory.write("delivery-register.md", f"待办{index}", confirmed=True, expected_content="原待办")
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert sum(executor.map(update, range(4))) == 1


def test_memory_replace_failure_preserves_original(tmp_path, monkeypatch):
    memory = WorkspaceMemory(tmp_path)
    memory.write("profile.md", "原文", confirmed=True)

    def fail_replace(*args):
        raise OSError("磁盘故障")

    monkeypatch.setattr("os.replace", fail_replace)
    with pytest.raises(OSError):
        memory.write("profile.md", "新文", confirmed=True, expected_content="原文")
    assert memory.read("profile.md") == "原文"


def test_local_trace_failure_does_not_turn_successful_memory_write_into_error(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "initialize_telemetry", lambda: False)

    def unavailable(*args, **kwargs):
        raise OSError("磁盘故障")

    monkeypatch.setattr(TraceStore, "append", unavailable)
    result, error = server.execute_tool("dy_memory_write", {
        "trace_id": "trace-disk", "name": "profile.md", "content": "确认事实", "confirmed": True,
    }, tmp_path)
    assert error is None
    assert result["written"] is True
    assert WorkspaceMemory(tmp_path).read("profile.md") == "确认事实"
    result, error = server.execute_tool("dy_trace_event", {
        "trace_id": "trace-disk", "event": {"event": "inspection.result"},
    }, tmp_path)
    assert error is None
    assert result["stored"] is False


def test_memory_conflict_is_actionable_tool_error(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(server, "initialize_telemetry", lambda: False)
    WorkspaceMemory(tmp_path).write("profile.md", "当前事实", confirmed=True)
    result, error = server.execute_tool("dy_memory_write", {
        "trace_id": "memory-conflict", "name": "profile.md", "content": "旧事实", "confirmed": True,
        "expected_content": "过时快照",
    }, tmp_path)
    assert result is None
    assert error["code"] == -32602
    assert "冲突" in error["message"]


def test_scoped_research_is_accessible_through_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(server, "initialize_telemetry", lambda: False)
    write_record(tmp_path, research_data())
    write_record(tmp_path, research_data(scope="公司B"))
    result, error = server.execute_tool("dy_module_memory_read", {
        "trace_id": "scope-read", "module": "account", "check_order": "1", "research_id": "01", "scope": "公司A",
    }, tmp_path)
    assert error is None
    assert result["records"][0]["scope"] == "公司A"
    result, error = server.execute_tool("dy_module_memory_read", {
        "trace_id": "scope-ambiguous", "module": "account", "check_order": "1", "research_id": "01",
    }, tmp_path)
    assert result is None
    assert error["code"] == -32602
