"""审查问题的可观察行为回归。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
import server
from mcp_helpers import listed_tools
from dy_superpowers_mcp.tools import runtime
from dy_superpowers_mcp.odoo_jsonrpc import OdooSettings
from dy_superpowers_mcp.trace import TraceStore


@pytest.mark.parametrize('password', [' password ', '\tpassword\n', '   '])
def test_odoo_environment_preserves_password_verbatim(monkeypatch, password):
    for name, value in {'APP_URL': ' http://odoo ',
                        'DB_NAME': ' db ', 'APP_USER': ' u ', 'APP_PASSWORD': password}.items():
        monkeypatch.setenv(name, value)
    settings = OdooSettings.from_environment()
    assert settings.password == password
    assert settings.url == 'http://odoo'
    assert settings.database == 'db'
    assert settings.username == 'u'


@pytest.mark.parametrize('password', [None, ''])
def test_odoo_environment_rejects_missing_password(monkeypatch, password):
    for name, value in {'APP_URL': 'http://odoo',
                        'DB_NAME': 'db', 'APP_USER': 'u'}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv('APP_PASSWORD', raising=False)
    if password is not None:
        monkeypatch.setenv('APP_PASSWORD', password)
    with pytest.raises(ValueError, match='APP_PASSWORD'):
        OdooSettings.from_environment()


@pytest.mark.parametrize('url', ['odoo:8069', 'file:///tmp/data', 'http://u:p@odoo',
                                  'https://odoo/#secret', 'https://odoo/?secret=1',
                                  'http://127.0.0.1:8069', 'http://10.0.0.1:8069'])
def test_odoo_rejects_untrusted_or_malformed_url(monkeypatch, url):
    monkeypatch.delenv('APP_URL', raising=False)
    with pytest.raises(ValueError):
        OdooSettings(url, 'db', 'u', 'p')


def test_explicit_internal_odoo_origin_is_supported(monkeypatch):
    monkeypatch.setenv('APP_URL', 'http://10.0.0.1:8069')
    assert OdooSettings('http://10.0.0.1:8069/odoo', 'db', 'u', 'p').url.endswith('/odoo')
    with pytest.raises(ValueError):
        OdooSettings('http://10.0.0.1:8068', 'db', 'u', 'p')


def test_trace_storage_has_workspace_wide_quota(tmp_path):
    store = TraceStore(tmp_path, root=tmp_path / 'codex', max_bytes=600, max_age_days=30)
    for index in range(30):
        store.append(f'trace-{index}', {'event': 'tool.start'})
    files = list(store.directory.glob('*.jsonl'))
    assert sum(path.stat().st_size for path in files) <= 600
    assert store.read('trace-29')
    assert store.read('trace-0') == []


def test_workspace_can_be_explicit_and_must_exist(tmp_path, monkeypatch):
    monkeypatch.setenv('DY_WORKSPACE_ROOT', str(tmp_path))
    assert server.resolve_workspace([]) == tmp_path.resolve()
    with pytest.raises(ValueError):
        server.resolve_workspace(['--workspace', str(tmp_path / 'missing')])


def test_legacy_search_alias_is_removed(tmp_path):
    tools = listed_tools()
    assert 'dy_odoo_read' not in {tool['name'] for tool in tools}
    assert 'dy_odoo_record_search' in {tool['name'] for tool in tools}
    _, error = server.execute_tool('dy_odoo_read', {'trace_id': 'alias', 'model': 'account.move'}, tmp_path)
    assert error['code'] == -32602


def test_plan_shape_only_tool_is_not_published(tmp_path):
    tools = listed_tools()
    assert 'dy_validate_plan' not in {tool['name'] for tool in tools}


def test_self_reported_trace_cannot_create_odoo_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    result, error = server.execute_tool('dy_trace_event', {'trace_id': 'forged', 'event': {
        'event': 'odoo.read', 'operation': 'search_read', 'status': 'ok', 'model': 'account.move',
    }}, tmp_path)
    assert result is None
    assert error['code'] == -32602


def test_self_reported_trace_cannot_set_tool_status_or_span(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    result, error = server.execute_tool('dy_trace_event', {'trace_id': 'manual', 'event': {
        'event': 'inspection.note', 'status': 'ok', 'tool': 'dy_odoo_record_search', 'span_id': 'forged',
    }}, tmp_path)
    assert error is None
    events = TraceStore(tmp_path).read('manual')
    event = next(item for item in events if item['event'] == 'inspection.note')
    assert 'status' not in event
    assert event['tool'] == 'dy_trace_event'
    assert event['span_id'] != 'forged'


def test_builtin_inspection_research_rejects_unknown_question_key(tmp_path, monkeypatch):
    from dy_superpowers_mcp.qa_templates import load_template
    from dy_superpowers_mcp.qa_store import read_qa
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    result, error = server.execute_tool('dy_research_answers_save', {
        'trace_id': 'unknown-question', 'module': 'expense', 'scope': '公司A',
        'template_revision': load_template('expense')['revision'],
        'batch_id': 'unknown-question', 'confirmed': True,
        'answers': [{'question_id': '999', 'answer': '不存在的问题不得写入',
                     'expected_revision': None, 'research_complete': True, 'attachments': []}]}, tmp_path)
    assert result is None and error and error['code'] == -32602
    assert '问题' in error['message']
    assert read_qa(tmp_path, 'expense')['records'] == []


def test_odoo_connection_reused_and_credentials_invalidate_cache(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_URL', 'http://odoo')
    for name, value in {'APP_URL': 'http://odoo', 'DB_NAME': 'db', 'APP_USER': 'u', 'APP_PASSWORD': 'p'}.items():
        monkeypatch.setenv(name, value)
    created, closed = [], []

    class Client:
        def __init__(self, settings):
            created.append(settings)
        def close(self):
            closed.append(self)
        def search_read(self, *args):
            return []

    runtime.clear_odoo_client()
    monkeypatch.setattr(runtime, 'OdooReadClient', Client)
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    try:
        assert server.execute_tool('dy_odoo_record_search', {'trace_id': 'reuse', 'model': 'account.move'}, tmp_path)[1] is None
        assert len(created) == 1
        monkeypatch.setenv('APP_PASSWORD', 'new')
        server.execute_tool('dy_odoo_record_search', {'trace_id': 'reuse', 'model': 'account.move'}, tmp_path)
        assert len(created) == 2
        assert len(closed) == 1
        runtime.odoo_client(tmp_path / 'other-workspace')
        assert len(created) == 3
        assert len(closed) == 2
    finally:
        runtime.clear_odoo_client()


def test_rotated_trace_read_is_ordered_and_retention_isolated(tmp_path, monkeypatch):
    import os
    import time
    store = TraceStore(tmp_path, root=tmp_path / 'codex', max_bytes=1600, max_age_days=1)
    other = TraceStore(tmp_path / 'other', root=tmp_path / 'codex')
    other_path = other.append('other', {'event': 'untouched'})
    for index in range(12):
        store.append('one', {'event': 'tool.start', 'count': index})
    assert list(store.directory.glob('one.*.jsonl.1'))
    counts = [event['count'] for event in store.read('one')]
    assert counts == sorted(counts)
    assert counts[-1] == 11
    for path in store.directory.glob('*.jsonl*'):
        os.utime(path, (time.time() - 86401,) * 2)
    # 改动 mtime 不应延长或缩短按事件日期定义的保留期。
    assert store.read('one')
    from datetime import datetime, timedelta, timezone
    from dy_superpowers_mcp import trace
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(timezone.utc) + timedelta(days=2)
    monkeypatch.setattr(trace, 'datetime', Clock)
    assert store.read('one') == []
    assert other_path.is_file()


def test_odoo_error_response_reports_call_failure_not_format_error():
    """错误响应即使不回显请求 id，也必须报「调用失败」而非「格式无效」。"""
    import threading
    from dy_superpowers_mcp.odoo_jsonrpc import OdooReadClient, OdooRPCError

    class ErrorNoIdSession:
        def post(self, *args, **kwargs):
            class R:
                def raise_for_status(self): pass
                def json(self): return {"jsonrpc": "2.0", "id": None,
                                        "error": {"code": 400, "message": "db 不存在"}}
            return R()
        def close(self): pass

    client = OdooReadClient.__new__(OdooReadClient)
    client.__dict__.update(_session=ErrorNoIdSession(), _lock=threading.RLock(),
                           _request_id=0, settings=None, uid=1, _rpc_url='http://odoo/jsonrpc')
    with pytest.raises(OdooRPCError, match='调用失败'):
        client._rpc('object', 'execute_kw', [])


def test_search_read_preserves_odoo_rpc_error_type():
    """OdooRPCError 不被包装成通用请求失败，保留服务端错误语义分层。"""
    import threading
    from dy_superpowers_mcp.odoo_jsonrpc import OdooReadClient, OdooRPCError

    class GarbageSession:
        def post(self, *args, **kwargs):
            raise ConnectionError("dead")
        def close(self): pass

    client = OdooReadClient.__new__(OdooReadClient)
    client.__dict__.update(_session=GarbageSession(), _lock=threading.RLock(),
                           _request_id=0, settings=None, uid=1)
    try:
        client._rpc("object", "execute_kw", [])
    except OdooRPCError as exc:
        marker = object()
        original = exc.__context__
        assert isinstance(original, OdooRPCError) or "JSON-RPC" in str(exc)
    else:
        raise AssertionError("应当抛出 OdooRPCError")
