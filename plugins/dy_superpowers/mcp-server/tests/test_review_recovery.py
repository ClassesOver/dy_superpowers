"""调研修复、追踪保留期与本地追踪健壮性的回归验证。"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
import server
from dy_superpowers_mcp import trace
from dy_superpowers_mcp.tools.runtime import trace_scope
from dy_superpowers_mcp.memory import WorkspaceMemory


def test_trace_scope_does_not_suppress_business_exception(tmp_path):
    with pytest.raises(ValueError, match='业务失败'):
        with trace_scope('recovery'):
            raise ValueError('业务失败')


def data(**updates):
    return dict(module='expense', check_order='1', research_id='01', config_item='应用安装',
                conclusion='确认结论', scope='公司A', confirmed=True, confirmed_by='顾问',
                confirmed_at='2026-10-21', **updates)


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


@pytest.mark.parametrize('filename, content, warns', [
    # 内容损坏但日期有效的文件不在清理时读取，保留原文且不告警。
    (f'interrupted.{datetime.now(timezone.utc).date().isoformat()}.jsonl', b'\xe4\xb8', False),
    ('old.2026-99-99.jsonl', b'{}\n', True),
])
def test_damaged_trace_file_does_not_block_unrelated_tools(tmp_path, monkeypatch, capsys, filename, content, warns):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    store = trace.TraceStore(tmp_path)
    store.directory.mkdir(parents=True)
    damaged = store.directory / filename
    damaged.write_bytes(content)

    result, error = server.execute_tool('dy_session_initialize', {'trace_id': 'unrelated', 'source': 'startup'}, tmp_path)

    assert error is None
    assert '初始化' in result
    assert store.read('unrelated')[-1]['status'] == 'ok'
    assert damaged.read_bytes() == content
    assert ('损坏' in capsys.readouterr().err) is warns


@pytest.mark.parametrize('filename, content', [
    ('interrupted.2026-10-01.jsonl', b'\xe4\xb8'),
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
