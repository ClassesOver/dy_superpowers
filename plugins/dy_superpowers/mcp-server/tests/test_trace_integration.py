"""验证真实 SDK 输出的链路层级、项目筛选字段与敏感数据边界。"""

import json
import sys
from pathlib import Path

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
import server
from dy_superpowers_mcp import telemetry
from dy_superpowers_mcp.memory import WorkspaceMemory
from dy_superpowers_mcp.odoo_jsonrpc import OdooRPCError
from dy_superpowers_mcp.trace import TraceStore


@pytest.fixture
def spans(tmp_path, monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(telemetry, '_tracer', provider.get_tracer('trace-test'))
    monkeypatch.setattr(server, 'initialize_telemetry', lambda: True)
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    yield exporter
    provider.shutdown()


def test_each_call_has_complete_tree_and_workflow_groups_across_calls(tmp_path, spans):
    WorkspaceMemory(tmp_path).write('profile.md', '项目背景', confirmed=True)
    for _ in range(2):
        result, error = server.execute_tool('dy_memory_read', {'trace_id': 'project-flow', 'name': 'profile.md'}, tmp_path)
        assert error is None and result == '项目背景'
    exported = spans.get_finished_spans()
    roots = [span for span in exported if span.parent is None]
    assert len(roots) == 2
    assert {span.name for span in roots} == {'dy_memory_read'}
    assert len({span.context.trace_id for span in roots}) == 2
    assert len({span.attributes['dy.workflow_id'] for span in roots}) == 1
    assert len({span.attributes['dy.call_id'] for span in roots}) == 2
    for root in roots:
        child, = [span for span in exported if span.parent and span.parent.span_id == root.context.span_id]
        assert child.name == 'memory.read'
        assert child.context.trace_id == root.context.trace_id
        assert child.status.status_code == root.status.status_code == StatusCode.OK
        assert root.attributes['dy.status'] == 'ok'
        assert root.attributes['dy.memory_file'] == 'profile.md'
        assert root.attributes['dy.category'] == 'memory'
        assert root.attributes['dy.duration_ms'] >= 0


def test_local_events_link_to_actual_remote_spans(tmp_path, spans):
    result, error = server.execute_tool('dy_memory_read', {'trace_id': 'links', 'name': 'profile.md'}, tmp_path)
    assert error is None
    exported = spans.get_finished_spans()
    by_id = {f'{span.context.span_id:016x}': span for span in exported}
    events = TraceStore(tmp_path).read('links')
    assert len(events) == 3
    for event in events:
        span = by_id[event['otel_span_id']]
        assert event['otel_trace_id'] == f'{span.context.trace_id:032x}'
        assert event['workflow_id'] == span.attributes['dy.workflow_id']


def test_workspace_separates_workflows_without_exporting_paths(tmp_path, spans):
    for project in ('project-a', 'project-b'):
        workspace = tmp_path / project
        workspace.mkdir()
        assert server.execute_tool('dy_session_initialize', {'trace_id': 'same-flow', 'source': 'resume'}, workspace)[1] is None
    roots = [span for span in spans.get_finished_spans() if span.parent is None]
    assert len({span.attributes['dy.workspace_id'] for span in roots}) == 2
    assert len({span.attributes['dy.workflow_id'] for span in roots}) == 2
    assert str(tmp_path) not in str([dict(span.attributes) for span in spans.get_finished_spans()])


def test_failure_sets_error_on_tree_without_exporting_exception_body(tmp_path, spans):
    result, error = server.execute_tool('dy_memory_write', {
        'trace_id': 'denied', 'name': 'profile.md', 'content': 'password=private', 'confirmed': False,
    }, tmp_path)
    assert result is None and error['code'] == -32602
    exported = spans.get_finished_spans()
    assert len(exported) == 2
    assert all(span.status.status_code == StatusCode.ERROR for span in exported)
    root, = [span for span in exported if span.parent is None]
    assert root.attributes['dy.error_code'] == -32602
    assert root.attributes['dy.status'] == 'error'
    assert all(event.name != 'exception' for span in exported for event in span.events)
    assert 'private' not in str([(dict(span.attributes), span.events, span.status.description) for span in exported])


def test_research_revision_is_filterable_and_body_stays_in_events(tmp_path, spans):
    result, error = server.execute_tool('dy_research_record_write', {
        'trace_id': 'research', 'module': 'expense', 'check_order': '1', 'research_id': '01',
        'config_item': '应用安装', 'scope': '本期', 'conclusion': '本期启用费用模块',
        'confirmed': True, 'confirmed_by': 'private-consultant', 'confirmed_at': '2026-10-22',
    }, tmp_path)
    assert error is None
    root, = [span for span in spans.get_finished_spans() if span.parent is None]
    assert root.attributes['dy.module'] == 'expense'
    assert root.attributes['dy.check_key'] == 'expense:1:01'
    assert root.attributes['dy.scope_id'] == result['scope_id']
    assert root.attributes['dy.revision'] == result['revision']
    assert root.attributes['dy.research_status'] == 'confirmed'
    assert 'dy.research_content' not in root.attributes
    body = next(event.attributes['dy.research_content'] for event in root.events if event.name == 'research.write')
    assert '本期启用费用模块' in body and 'private-consultant' not in body


def test_failed_odoo_query_still_records_redacted_domain(tmp_path, monkeypatch, spans):
    class UnreachableOdoo:
        def search_read(self, *args):
            raise OdooRPCError('password=private-server-error')
    monkeypatch.setattr(server, '_odoo_client', lambda workspace: UnreachableOdoo())
    result, error = server.execute_tool('dy_odoo_record_search', {
        'trace_id': 'odoo-failed', 'model': 'res.partner',
        'args': [[['email', '=', 'private@example.com']]], 'kwargs': {'fields': ['id'], 'limit': 2},
    }, tmp_path)
    assert result is None and error['code'] == -32603
    root, = [span for span in spans.get_finished_spans() if span.parent is None]
    assert json.loads(root.attributes['dy.domain']) == [['email', '=', '[REDACTED]']]
    assert root.attributes['dy.model'] == 'res.partner'
    assert root.attributes['dy.error_type'] == 'tool_error'
    events = TraceStore(tmp_path).read('odoo-failed')
    assert any(event['event'] == 'odoo.query' for event in events)
    assert 'private@example.com' not in str(events)
    assert 'private-server-error' not in str([(dict(span.attributes), span.events) for span in spans.get_finished_spans()])


def test_fallback_and_business_gate_are_distinct_from_tool_failure(tmp_path, monkeypatch, spans):
    monkeypatch.setattr(server.BasicMemoryAdapter, 'search', lambda self, query, *, module=None: {
        'available': False, 'backend': 'local-fallback', 'results': [], 'has_more': False,
    })
    assert server.execute_tool('dy_memory_search', {'trace_id': 'fallback', 'query': '费用'}, tmp_path)[1] is None
    assert server.execute_tool('dy_inspection_validate', {'trace_id': 'gate', 'report': {}}, tmp_path)[1] is None
    roots = {span.name: span for span in spans.get_finished_spans() if span.parent is None}
    assert roots['dy_memory_search'].attributes['dy.backend_status'] == 'fallback'
    gate = roots['dy_inspection_validate']
    assert gate.status.status_code == StatusCode.OK
    assert gate.attributes['dy.valid'] is False
    assert gate.attributes['dy.result'] == '待确认'
    assert gate.attributes['dy.error_count'] > 0


def test_manual_event_cannot_forge_remote_identity_or_lifecycle(tmp_path, spans):
    result, error = server.execute_tool('dy_trace_event', {'trace_id': 'manual', 'event': {
        'event': 'inspection.check', 'tool': 'dy_memory_write', 'workspace_id': 'forged',
        'workflow_id': 'forged', 'span_id': 'forged', 'otel_trace_id': 'forged',
        'status': 'error', 'error_code': -32603, 'memory_content': 'password=private',
        'module': 'expense', 'check_key': 'expense:1:01', 'result': '待确认',
    }}, tmp_path)
    assert error is None and result['stored']
    root, = [span for span in spans.get_finished_spans() if span.parent is None]
    assert root.attributes['dy.tool'] == 'dy_trace_event'
    assert root.attributes['dy.workspace_id'] != 'forged'
    assert 'dy.error_code' not in root.attributes
    assert root.status.status_code == StatusCode.OK
    assert root.attributes['dy.check_key'] == 'expense:1:01'
    assert root.attributes['dy.result'] == '待确认'
    event = next(event for event in TraceStore(tmp_path).read('manual') if event['event'] == 'inspection.check')
    assert event['span_id'] != 'forged' and event['tool'] == 'dy_trace_event'


def test_concurrent_workspaces_do_not_mix_context(tmp_path, spans):
    from concurrent.futures import ThreadPoolExecutor

    def read(project):
        workspace = tmp_path / project
        workspace.mkdir()
        value, error = server.execute_tool('dy_memory_read', {'trace_id': 'parallel', 'name': 'profile.md'}, workspace)
        assert error is None
        return workspace, TraceStore(workspace).read('parallel')

    with ThreadPoolExecutor(max_workers=2) as pool:
        projects = list(pool.map(read, ('project-a', 'project-b')))
    roots = {span.attributes['dy.workspace_id']: span for span in spans.get_finished_spans() if span.parent is None}
    assert len(roots) == 2
    for workspace, events in projects:
        root = roots[events[0]['workspace_id']]
        assert {event['otel_trace_id'] for event in events} == {f'{root.context.trace_id:032x}'}
        assert {event['workflow_id'] for event in events} == {root.attributes['dy.workflow_id']}


def test_disabled_telemetry_keeps_local_workflow_without_remote_ids(tmp_path, monkeypatch, spans):
    monkeypatch.setattr(telemetry, '_tracer', None)
    assert server.execute_tool('dy_memory_read', {'trace_id': 'offline', 'name': 'profile.md'}, tmp_path)[1] is None
    events = TraceStore(tmp_path).read('offline')
    assert len(events) == 3
    assert all(event['workflow_id'] for event in events)
    assert all('otel_span_id' not in event and 'otel_trace_id' not in event for event in events)
    assert not spans.get_finished_spans()


def test_inspection_context_links_memory_query_to_check_item(tmp_path, spans):
    context = {'skill': 'expense-inspection', 'module': 'expense', 'check_order': '1',
               'research_id': '01', 'scope_id': 'scope-a', 'revision': 'revision-a'}
    value, error = server.execute_tool('dy_memory_read', {
        'trace_id': 'inspection-flow', 'name': 'profile.md', 'trace_context': context,
    }, tmp_path)
    assert error is None
    for span in spans.get_finished_spans():
        assert span.attributes['dy.check_key'] == 'expense:1:01'
        assert span.attributes['dy.skill'] == 'expense-inspection'
        assert span.attributes['dy.scope_id'] == 'scope-a'
    assert all(event['check_key'] == 'expense:1:01' for event in TraceStore(tmp_path).read('inspection-flow'))


@pytest.mark.parametrize('context', [{'workspace_id': 'forged'}, {'module': 'password=private'}, [], {'scope_id': 3}])
def test_invalid_trace_context_is_rejected_without_recording_secrets(tmp_path, spans, context):
    value, error = server.execute_tool('dy_memory_read', {
        'trace_id': 'invalid-context', 'name': 'profile.md', 'trace_context': context,
    }, tmp_path)
    assert value is None and error['code'] == -32602
    assert 'private' not in str([(dict(span.attributes), span.events) for span in spans.get_finished_spans()])


def test_blocked_remote_export_does_not_block_next_tool_call(tmp_path, monkeypatch):
    from threading import Event
    from concurrent.futures import ThreadPoolExecutor
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult

    exporting, release = Event(), Event()

    class SlowRemote(SpanExporter):
        def export(self, spans):
            exporting.set()
            release.wait(timeout=10)
            return SpanExportResult.FAILURE

    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(SlowRemote(), schedule_delay_millis=1))
    monkeypatch.setattr(telemetry, '_tracer', provider.get_tracer('offline-test'))
    monkeypatch.setattr(server, 'initialize_telemetry', lambda: True)
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    try:
        assert server.execute_tool('dy_memory_read', {'trace_id': 'slow-export', 'name': 'profile.md'}, tmp_path)[1] is None
        assert exporting.wait(timeout=3)
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(server.execute_tool, 'dy_memory_read', {
                'trace_id': 'slow-export', 'name': 'profile.md',
            }, tmp_path)
            assert result.result(timeout=3)[1] is None
        assert not release.is_set()
        assert TraceStore(tmp_path).read('slow-export')[-1]['status'] == 'ok'
    finally:
        release.set()
        provider.shutdown()


def test_failed_root_does_not_attach_plugin_operations_to_external_span(tmp_path, monkeypatch, spans):
    actual_tracer = telemetry._tracer

    class FailingRoot:
        def start_as_current_span(self, name, **kwargs):
            if name == 'dy_memory_read':
                raise RuntimeError('根节点不可用')
            return actual_tracer.start_as_current_span(name, **kwargs)

    monkeypatch.setattr(telemetry, '_tracer', FailingRoot())
    with actual_tracer.start_as_current_span('external-host'):
        assert server.execute_tool('dy_memory_read', {'trace_id': 'root-failed', 'name': 'profile.md'}, tmp_path)[1] is None
    assert [span.name for span in spans.get_finished_spans()] == ['external-host']
    assert all('otel_trace_id' not in event for event in TraceStore(tmp_path).read('root-failed'))
