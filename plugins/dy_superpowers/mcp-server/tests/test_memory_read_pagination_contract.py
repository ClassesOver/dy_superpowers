"""模块记忆分页返回可直接复用的游标，避免调用方猜测。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))

import server
from dy_superpowers_mcp.research import write_record


def test_memory_read_returns_next_offset_until_last_page(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    monkeypatch.setattr(server, 'initialize_telemetry', lambda: False)
    for order in ('1', '2', '3'):
        write_record(tmp_path, dict(module='expense', check_order=order, research_id='01',
                     config_item='应用', conclusion='本期启用', scope='中心院区',
                     confirmed=True, confirmed_by='测试', confirmed_at='2026-10-28'))
    arguments = dict(legacy=True, trace_id='pagination', module='expense', limit=2)
    first, error = server.execute_tool('dy_module_memory_read', arguments, tmp_path)
    assert error is None
    assert len(first['records']) == 2 and first['has_more'] is True
    assert first['next_offset'] == 2
    last, error = server.execute_tool('dy_module_memory_read',
                                      dict(arguments, offset=first['next_offset']), tmp_path)
    assert error is None
    assert len(last['records']) == 1 and last['has_more'] is False
    assert last['next_offset'] is None
    assert [r['check_order'] for r in first['records'] + last['records']] == ['1', '2', '3']
