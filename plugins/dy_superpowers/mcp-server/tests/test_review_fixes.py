"""审查问题的可观察行为回归。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
import server
from dy_superpowers_mcp.odoo_jsonrpc import OdooSettings
from dy_superpowers_mcp.memory import module_memory_path
from dy_superpowers_mcp import telemetry
from dy_superpowers_mcp.trace import TraceStore
from dy_superpowers_mcp.research import read_record


@pytest.mark.parametrize('password', [' password ', '\tpassword\n', '   '])
def test_odoo_environment_preserves_password_verbatim(monkeypatch, password):
    for name, value in {'ODOO_URL': ' http://odoo ', 'DY_ODOO_ALLOWED_ORIGINS': 'http://odoo',
                        'ODOO_DB': ' db ', 'ODOO_USERNAME': ' u ', 'ODOO_PASSWORD': password}.items():
        monkeypatch.setenv(name, value)
    settings = OdooSettings.from_environment()
    assert settings.password == password
    assert settings.url == 'http://odoo'
    assert settings.database == 'db'
    assert settings.username == 'u'


@pytest.mark.parametrize('password', [None, ''])
def test_odoo_environment_rejects_missing_password(monkeypatch, password):
    for name, value in {'ODOO_URL': 'http://odoo', 'DY_ODOO_ALLOWED_ORIGINS': 'http://odoo',
                        'ODOO_DB': 'db', 'ODOO_USERNAME': 'u'}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv('ODOO_PASSWORD', raising=False)
    if password is not None:
        monkeypatch.setenv('ODOO_PASSWORD', password)
    with pytest.raises(ValueError, match='ODOO_PASSWORD'):
        OdooSettings.from_environment()


@pytest.mark.parametrize('url', ['odoo:8069', 'file:///tmp/data', 'http://u:p@odoo',
                                  'https://odoo/#secret', 'https://odoo/?secret=1',
                                  'http://127.0.0.1:8069', 'http://10.0.0.1:8069'])
def test_odoo_rejects_untrusted_or_malformed_url(monkeypatch, url):
    monkeypatch.delenv('DY_ODOO_ALLOWED_ORIGINS', raising=False)
    with pytest.raises(ValueError):
        OdooSettings(url, 'db', 'u', 'p')


def test_explicit_internal_odoo_origin_is_supported(monkeypatch):
    monkeypatch.setenv('DY_ODOO_ALLOWED_ORIGINS', 'http://10.0.0.1:8069')
    assert OdooSettings('http://10.0.0.1:8069/odoo', 'db', 'u', 'p').url.endswith('/odoo')
    with pytest.raises(ValueError):
        OdooSettings('http://10.0.0.1:8068', 'db', 'u', 'p')


def test_invalid_otlp_configuration_does_not_initialize_exporter(monkeypatch):
    monkeypatch.setenv('OTEL_EXPORTER_OTLP_TRACES_ENDPOINT', 'http://169.254.169.254/latest')
    monkeypatch.delenv('OTEL_SDK_DISABLED', raising=False)
    monkeypatch.setattr(telemetry, '_initialized', False)
    monkeypatch.setattr(telemetry, '_tracer', None)
    assert telemetry.initialize_telemetry() is False


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


def test_orphan_research_marker_is_never_missing(tmp_path):
    path = module_memory_path(tmp_path, 'account')
    path.parent.mkdir(parents=True)
    path.write_text('<!-- dy-research-record:start -->\n## account / 项目（1 + 01）\n')
    with pytest.raises(ValueError, match='损坏'):
        read_record(tmp_path, 'account', '1', '01')


def test_legacy_search_alias_is_removed(tmp_path):
    tools = server.handle({'id': 1, 'method': 'tools/list'}, tmp_path)['result']['tools']
    assert 'dy_odoo_read' not in {tool['name'] for tool in tools}
    assert 'dy_odoo_record_search' in {tool['name'] for tool in tools}
    _, error = server.execute_tool('dy_odoo_read', {'trace_id': 'alias', 'model': 'account.move'}, tmp_path)
    assert error['code'] == -32602


def test_plan_shape_only_tool_is_not_published(tmp_path):
    tools = server.handle({'id': 1, 'method': 'tools/list'}, tmp_path)['result']['tools']
    assert 'dy_validate_plan' not in {tool['name'] for tool in tools}


def test_self_reported_trace_cannot_create_odoo_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    monkeypatch.setattr(server, 'initialize_telemetry', lambda: False)
    result, error = server.execute_tool('dy_trace_event', {'trace_id': 'forged', 'event': {
        'event': 'odoo.read', 'operation': 'search_read', 'status': 'ok', 'model': 'account.move',
    }}, tmp_path)
    assert result is None
    assert error['code'] == -32602


def test_self_reported_trace_cannot_set_tool_status_or_span(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    monkeypatch.setattr(server, 'initialize_telemetry', lambda: False)
    result, error = server.execute_tool('dy_trace_event', {'trace_id': 'manual', 'event': {
        'event': 'inspection.note', 'status': 'ok', 'tool': 'dy_odoo_record_search', 'span_id': 'forged',
    }}, tmp_path)
    assert error is None
    events = TraceStore(tmp_path).read('manual')
    event = next(item for item in events if item['event'] == 'inspection.note')
    assert 'status' not in event
    assert event['tool'] == 'dy_trace_event'
    assert event['span_id'] != 'forged'


def test_module_memory_list_filters_before_pagination(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    monkeypatch.setattr(server, 'initialize_telemetry', lambda: False)
    from dy_superpowers_mcp.research import write_record
    for scope, scope_id in [('A', 'scope-a'), ('B', 'scope-b'), ('A2', 'scope-a2')]:
        write_record(tmp_path, dict(module='expense', check_order='1', research_id='01', config_item='项目',
                                    conclusion=f'结论{scope}', scope=scope, scope_id=scope_id,
                                    confirmed_by='顾问', confirmed_at='2026-10-21', confirmed=True))
    result, error = server.execute_tool('dy_module_memory_read', {
        'trace_id': 'scope-page', 'module': 'expense', 'scope_id': 'scope-a', 'limit': 1,
    }, tmp_path)
    assert error is None
    assert [record['scope_id'] for record in result['records']] == ['scope-a']
    assert result['has_more'] is False


def test_module_memory_rejects_scope_and_scope_id_mismatch(tmp_path):
    from dy_superpowers_mcp.research import read_module_records, read_record, write_record
    write_record(tmp_path, dict(module='expense', check_order='1', research_id='01', config_item='项目',
                                conclusion='结论', scope='A', scope_id='scope-a',
                                confirmed_by='顾问', confirmed_at='2026-10-21', confirmed=True))
    with pytest.raises(ValueError, match='scope'):
        read_module_records(tmp_path, 'expense', scope='A', scope_id='scope-b')
    with pytest.raises(ValueError, match='scope'):
        read_record(tmp_path, 'expense', '1', '01', scope='A', scope_id='scope-b')


def test_module_research_symlink_outside_workspace_is_rejected(tmp_path):
    outside = tmp_path.parent / f'{tmp_path.name}-outside.md'
    outside.write_text('工作区外秘密')
    path = module_memory_path(tmp_path, 'expense')
    path.parent.mkdir(parents=True)
    path.symlink_to(outside)
    from dy_superpowers_mcp.research import read_module_records
    with pytest.raises(ValueError, match='符号链接'):
        read_module_records(tmp_path, 'expense')
    outside.unlink()


def test_module_memory_directory_symlink_is_rejected_even_inside_workspace(tmp_path):
    linked = tmp_path / '.dy_superpowers'
    destination = tmp_path / 'another-project-memory'
    destination.mkdir()
    linked.symlink_to(destination, target_is_directory=True)
    with pytest.raises(ValueError, match='符号链接'):
        module_memory_path(tmp_path, 'expense')


def test_same_day_future_confirmation_timestamp_is_rejected(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    import dy_superpowers_mcp.research as research
    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(research, 'datetime', FixedClock)
    from dy_superpowers_mcp.research import write_record
    with pytest.raises(ValueError, match='当前时间'):
        write_record(tmp_path, dict(module='expense', check_order='1', research_id='01', config_item='项目',
                                    conclusion='结论', scope='公司A', confirmed_by='顾问',
                                    confirmed_at='2026-10-22T23:59:59+00:00', confirmed=True))


def test_builtin_inspection_research_rejects_unknown_matrix_key(tmp_path):
    from dy_superpowers_mcp.research import write_record
    with pytest.raises(ValueError, match='矩阵'):
        write_record(tmp_path, dict(module='expense', check_order='999', research_id='999', config_item='不存在',
                                    conclusion='结论', scope='公司A', confirmed_by='顾问',
                                    confirmed_at='2026-10-21', confirmed=True))


def test_odoo_connection_reused_and_credentials_invalidate_cache(tmp_path, monkeypatch):
    monkeypatch.setenv('DY_ODOO_ALLOWED_ORIGINS', 'http://odoo')
    for name, value in {'ODOO_URL': 'http://odoo', 'ODOO_DB': 'db', 'ODOO_USERNAME': 'u', 'ODOO_PASSWORD': 'p'}.items():
        monkeypatch.setenv(name, value)
    created, closed = [], []

    class Client:
        def __init__(self, settings):
            created.append(settings)
        def close(self):
            closed.append(self)
        def search_read(self, *args):
            return []

    server._clear_odoo_client()
    monkeypatch.setattr(server, 'OdooReadClient', Client)
    monkeypatch.setenv('OTEL_SDK_DISABLED', 'true')
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    try:
        assert server.execute_tool('dy_odoo_record_search', {'trace_id': 'reuse', 'model': 'account.move'}, tmp_path)[1] is None
        assert len(created) == 1
        monkeypatch.setenv('ODOO_PASSWORD', 'new')
        server.execute_tool('dy_odoo_record_search', {'trace_id': 'reuse', 'model': 'account.move'}, tmp_path)
        assert len(created) == 2
        assert len(closed) == 1
        server._odoo_client(tmp_path / 'other-workspace')
        assert len(created) == 3
        assert len(closed) == 2
    finally:
        server._clear_odoo_client()


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


def test_new_research_has_one_body_and_detects_edits(tmp_path):
    from dy_superpowers_mcp.research import write_record
    data = dict(module='account', check_order='1', research_id='01', config_item='项目',
                conclusion='唯一确认结论', scope='公司A', confirmed_by='顾问', confirmed_at='2026-10-21', confirmed=True)
    write_record(tmp_path, data)
    path = tmp_path / '.dy_superpowers/modules/account/research.md'
    text = path.read_text()
    assert text.count('唯一确认结论') == 1
    assert 'dy-research-record:data' not in text
    path.write_text(text.replace('唯一确认结论', '意外编辑'))
    with pytest.raises(ValueError, match='不一致'):
        read_record(tmp_path, 'account', '1', '01')


def test_authorized_otlp_has_no_redirects_or_environment_proxy(monkeypatch):
    import requests
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    monkeypatch.setattr(telemetry, '_initialized', False)
    monkeypatch.setattr(telemetry, '_tracer', None)
    monkeypatch.setattr(telemetry, '_provider', None)
    monkeypatch.delenv('OTEL_SDK_DISABLED', raising=False)
    monkeypatch.setenv('OTEL_EXPORTER_OTLP_TRACES_ENDPOINT', 'https://collector.example/v1/traces')
    monkeypatch.setenv('DY_OTLP_ALLOWED_ORIGINS', 'https://collector.example')
    monkeypatch.setattr(trace, 'set_tracer_provider', lambda *_: pytest.fail('不得注册全局 provider'))
    captured = []
    def initialize(self, *args, **kwargs):
        captured.append(kwargs)
    monkeypatch.setattr(OTLPSpanExporter, '__init__', initialize)
    monkeypatch.setattr(OTLPSpanExporter, 'shutdown', lambda self: None)
    monkeypatch.setattr(OTLPSpanExporter, 'export', lambda self, spans: None)
    monkeypatch.setattr(requests.Session, 'request', lambda self, *args, **kwargs: kwargs)
    try:
        assert telemetry.initialize_telemetry() is True
        assert captured[0]['session'].trust_env is False
        assert captured[0]['timeout'] == 1.0
        assert captured[0]['session'].post('https://collector.example')['allow_redirects'] is False
    finally:
        telemetry.shutdown_telemetry()
        telemetry._initialized = False
        telemetry._provider = telemetry._tracer = None


@pytest.mark.parametrize('damage', ['json', 'scope'])
def test_scoped_lookup_cannot_hide_damaged_new_record(tmp_path, damage):
    from dy_superpowers_mcp.research import write_record
    write_record(tmp_path, dict(module='account', check_order='1', research_id='01', config_item='项目',
                              conclusion='确认结论', scope='公司A', confirmed_by='顾问', confirmed_at='2026-10-21', confirmed=True))
    path = tmp_path / '.dy_superpowers/modules/account/research.md'
    text = path.read_text()
    text = text.replace('"scope": "公司A"', '"scope": "公司B"') if damage == 'scope' else text.replace('"conclusion":', '"conclusion"::')
    path.write_text(text)
    with pytest.raises(ValueError, match='损坏'):
        read_record(tmp_path, 'account', '1', '01', scope='公司A')
