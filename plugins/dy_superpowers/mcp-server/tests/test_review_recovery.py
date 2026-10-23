"""调研修复、追踪保留期与本地追踪健壮性的回归验证。"""

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
import server
from dy_superpowers_mcp import telemetry, trace
from dy_superpowers_mcp.memory import WorkspaceMemory
from dy_superpowers_mcp.research import read_record, write_record


def test_tool_span_does_not_suppress_business_exception(tmp_path):
    with pytest.raises(ValueError, match='业务失败'):
        with telemetry.tool_span('recovery', tmp_path, 'dy_memory_read'):
            raise ValueError('业务失败')


def data(**updates):
    return dict(module='expense', check_order='1', research_id='01', config_item='应用安装',
                conclusion='确认结论', scope='公司A', confirmed=True, confirmed_by='顾问',
                confirmed_at='2026-10-21', **updates)


def test_repeated_repairs_preserve_raw_history_without_recursive_growth(tmp_path):
    write_record(tmp_path, data())
    path = tmp_path / '.dy_superpowers/modules/expense/research.md'
    initial_size = path.stat().st_size
    revisions = []
    for index in range(7):
        text = path.read_text().replace('"conclusion": "确认结论"', f'"conclusion": "意外改动{index}"', 1)
        path.write_text(text)
        with pytest.raises(ValueError) as failure:
            read_record(tmp_path, 'expense', '1', '01')
        revisions.append(failure.value.revision)
        write_record(tmp_path, {**data(), 'repair': True, 'expected_revision': failure.value.revision})
    current = read_record(tmp_path, 'expense', '1', '01')
    assert len(current['history']) == 7
    for index, item in enumerate(current['history']):
        assert item['status'] == 'invalidated'
        assert hashlib.sha256(item['original_block'].encode()).hexdigest() == revisions[index]
        assert f'意外改动{index}' in item['original_block']
    assert path.stat().st_size < initial_size * 12


@pytest.mark.parametrize('damaged', [{'history': []}, {'scope': 17}, {'scope': '错误范围'}, {'scope_id': {'bad': 1}}, {'history': [None]}])
def test_repair_uses_confirmed_scope_when_prior_fields_are_damaged(tmp_path, damaged):
    write_record(tmp_path, data())
    path = tmp_path / '.dy_superpowers/modules/expense/research.md'
    text = path.read_text()
    prefix, rest = text.split('```json\n', 1)
    body, suffix = rest.split('\n```', 1)
    payload = json.loads(body)
    if damaged == {'history': []}:
        payload = damaged
    else:
        payload.update(damaged)
    path.write_text(prefix + '```json\n' + json.dumps(payload, ensure_ascii=False) + '\n```' + suffix)
    with pytest.raises(ValueError) as failure:
        read_record(tmp_path, 'expense', '1', '01')
    saved = write_record(tmp_path, {**data(), 'repair': True, 'expected_revision': failure.value.revision})
    current = read_record(tmp_path, 'expense', '1', '01', scope_id=saved['scope_id'])
    assert current['scope'] == '公司A'
    assert isinstance(current['scope_id'], str)
    assert current['status'] == 'confirmed'


def test_active_trace_does_not_extend_old_event_retention(tmp_path, monkeypatch):
    class Clock(datetime):
        now_value = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
        @classmethod
        def now(cls, tz=None):
            return cls.now_value
    monkeypatch.setattr(trace, 'datetime', Clock)
    store = trace.TraceStore(tmp_path, root=tmp_path / 'codex', max_age_days=2)
    store.append('ongoing', {'event': 'old'})
    Clock.now_value = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
    store.append('ongoing', {'event': 'retained'})
    Clock.now_value = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
    store.append('ongoing', {'event': 'new'})
    assert [item['event'] for item in store.read('ongoing')] == ['retained', 'new']
    assert 'old' not in ''.join(path.read_text() for path in store.directory.glob('*.jsonl*'))


def test_legacy_trace_retention_uses_event_time_and_keeps_backup(tmp_path):
    store = trace.TraceStore(tmp_path, root=tmp_path / 'codex', max_age_days=2)
    store.directory.mkdir(parents=True)
    current = datetime.now(timezone.utc).isoformat()
    path = store.directory / 'legacy.jsonl'
    path.write_text(json.dumps({'event': 'old', 'timestamp': '2000-01-01T00:00:00+00:00'}) + '\n'
                    + json.dumps({'event': 'new', 'timestamp': current}) + '\n')
    backup = store.directory / 'legacy.jsonl.1'
    backup.write_text(json.dumps({'event': 'backup', 'timestamp': current}) + '\n')
    assert [item['event'] for item in store.read('legacy')] == ['backup', 'new']
    assert 'old' not in path.read_text()


def test_repair_snapshot_stays_inside_workspace(tmp_path):
    project = tmp_path / 'project'
    write_record(project, data())
    path = project / '.dy_superpowers/modules/expense/research.md'
    before = path.read_text()
    current = read_record(project, 'expense', '1', '01')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (path.parent / 'history').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='当前工作区'):
        write_record(project, {**data(), 'repair': True, 'expected_revision': current['revision']})
    assert not list(outside.iterdir())
    assert path.read_text() == before


def test_repair_cannot_overwrite_another_scope(tmp_path):
    write_record(tmp_path, data())
    write_record(tmp_path, {**data(), 'scope': '公司B'})
    original = read_record(tmp_path, 'expense', '1', '01', scope='公司A')
    with pytest.raises(ValueError, match='范围.*冲突'):
        write_record(tmp_path, {**data(), 'scope': '公司B', 'repair': True, 'expected_revision': original['revision']})
    assert read_record(tmp_path, 'expense', '1', '01', scope='公司B')['scope'] == '公司B'


def test_corrupt_legacy_trace_does_not_block_new_trace(tmp_path, capsys):
    store = trace.TraceStore(tmp_path, root=tmp_path / 'codex')
    store.directory.mkdir(parents=True)
    legacy = store.directory / 'interrupted.jsonl'
    legacy.write_text('{"event":')
    store.append('current', {'event': 'ok'})
    assert store.read('current')[-1]['event'] == 'ok'
    assert legacy.read_text() == '{"event":'
    assert '损坏' in capsys.readouterr().err


@pytest.mark.parametrize('filename, content', [
    ('interrupted.jsonl', b'\xe4\xb8'),
    ('old.2026-99-99.jsonl', b'{}\n'),
])
def test_damaged_trace_file_does_not_block_unrelated_tools(tmp_path, monkeypatch, capsys, filename, content):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    monkeypatch.setattr(server, 'initialize_telemetry', lambda: False)
    store = trace.TraceStore(tmp_path)
    store.directory.mkdir(parents=True)
    damaged = store.directory / filename
    damaged.write_bytes(content)

    result, error = server.execute_tool('dy_session_initialize', {'trace_id': 'unrelated', 'source': 'startup'}, tmp_path)

    assert error is None
    assert '初始化' in result
    assert store.read('unrelated')[-1]['status'] == 'ok'
    assert damaged.read_bytes() == content
    assert '损坏' in capsys.readouterr().err


@pytest.mark.parametrize('filename, content', [
    ('interrupted.jsonl', b'\xe4\xb8'),
    ('old.2026-99-99.jsonl', b'{}\n'),
])
def test_damaged_trace_files_still_expire_by_file_time(tmp_path, filename, content):
    import os

    store = trace.TraceStore(tmp_path, root=tmp_path / 'codex')
    store.directory.mkdir(parents=True)
    damaged = store.directory / filename
    damaged.write_bytes(content)
    os.utime(damaged, (946684800, 946684800))

    store.append('current', {'event': 'ok'})

    assert not damaged.exists()
    assert store.read('current')[-1]['event'] == 'ok'


def test_missing_repair_snapshot_requires_backup_restore(tmp_path):
    write_record(tmp_path, data())
    current = read_record(tmp_path, 'expense', '1', '01')
    write_record(tmp_path, {**data(), 'repair': True, 'expected_revision': current['revision']})
    repaired = read_record(tmp_path, 'expense', '1', '01')
    reference = repaired['history'][0]['original_block_ref']
    (tmp_path / '.dy_superpowers' / 'modules' / 'expense' / reference).unlink()
    with pytest.raises(ValueError, match='恢复.*备份'):
        read_record(tmp_path, 'expense', '1', '01')
