"""调研闭环缺口回归；仅使用临时工作区，不修改项目业务记忆。"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
from dy_superpowers_mcp.research_progress import calculate_progress, export_unanswered_template, _guide_items
from dy_superpowers_mcp.wiki_sync import sync_wiki
import server


def test_mcp_forwards_only_required_business_environment_keys():
    config = json.loads((Path(__file__).parents[2] / '.mcp.json').read_text())
    forwarded = set(config['mcpServers']['dy-superpowers-odoo']['env_vars'])
    assert set(server.ENV_FILE_KEYS) <= forwarded
    assert {'DY_WORKSPACE_ROOT', 'FEISHU_CODEX_BOT_SENDER_USER_ID'} <= forwarded
    assert 'OPENAI_API_KEY' not in forwarded
    assert 'FEISHU_BOT_APP_SECRET' not in forwarded

script = Path(__file__).parents[2] / 'skills/module-research/scripts/research_sources.py'
spec = importlib.util.spec_from_file_location('repair_research_sources', script)
sources = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sources)


def test_export_filled_return_preserves_key_scope_evidence_and_unanswered(tmp_path):
    result = export_unanswered_template(tmp_path, 'general-ledger', 'roundtrip',
                                       records=[], scope='中心院区', confirmed=True)
    path = tmp_path / result['path']
    book = load_workbook(path)
    sheet = book.active
    sheet['J2'] = '中心院区2026年上线预算会计；依据：业务访谈记录'
    sheet['K2'] = '附件一'
    book.save(path)
    parsed = sources.parse_source(path, 'general-ledger')
    assert len(parsed['records']) == 1
    record = parsed['records'][0]
    assert record['research_id'] == str(sheet['D2'].value)
    assert record['check_order'] == sheet['B2'].value
    assert record['conclusion'] == sheet['J2'].value
    assert record['scope_fields'] == {'适用公司/院区': '', '适用业务范围': '', '上线阶段/期间': ''}
    assert record['attachment_materials'] == '附件一'
    assert record['can_write_confirmed'] is False
    assert len(parsed['unanswered']) == result['question_count'] - 1


def test_detail_pending_count_is_separate_from_candidate_count(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.append(['检查顺序', '配置项', '调研结果'])
    sheet.append(['GL-1-001', '应用', '01｜已配置'])
    detail = book.create_sheet('期间明细')
    detail.append(['上线期间', '待确认'])
    path = tmp_path / 'result.xlsx'
    book.save(path)
    parsed = sources.parse_source(path, 'general-ledger')
    assert parsed['coverage']['contains_pending_questions'] == 0
    assert parsed['coverage']['source_pending_cells'] == 1
    assert parsed['pending_locations'] == [{'sheet': '期间明细', 'cell': 'B1', 'hidden': False}]


def test_unknown_keys_and_missing_completeness_are_visible_and_not_counted():
    item = _guide_items('general-ledger')[0]
    records = [dict(module='general-ledger', status='confirmed', scope_id='a', **item),
               dict(module='general-ledger', status='confirmed', scope_id='a',
                    check_order='EXP-001', research_id='01', research_complete=True)]
    progress = calculate_progress('general-ledger', records, scope_id='a')
    assert progress['answered'] == 0
    assert len(progress['unmapped_records']) == 1
    assert len(progress['completeness_unknown']) == 1


@pytest.mark.parametrize('confirmed', [None, False])
def test_wiki_requires_explicit_authorization_before_local_or_remote_work(tmp_path, monkeypatch, confirmed):
    import dy_superpowers_mcp.wiki_sync as wiki
    monkeypatch.setattr(wiki, 'load_staged', lambda *_: pytest.fail('未授权不能读取发布清单'))
    with pytest.raises(PermissionError, match='确认'):
        if confirmed is None:
            sync_wiki(tmp_path)
        else:
            sync_wiki(tmp_path, confirmed=confirmed)


def test_wiki_failure_stops_dependent_work(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    result, error = server.execute_tool('dy_wiki_sync', {'trace_id': 'failure', 'confirmed': True}, tmp_path)
    assert error is not None
    assert result is None


def test_progress_reads_all_pages_before_routing(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    items = _guide_items('general-ledger')
    records = [dict(module='general-ledger', status='confirmed', scope_id='a',
                    check_order=f'legacy-{i}', research_id='01') for i in range(100)]
    records += [dict(module='general-ledger', status='confirmed', scope_id='a',
                     research_complete=True, **item) for item in items[:58]]
    monkeypatch.setattr(server, 'read_module_records',
                        lambda *args, offset=0, limit=100, **kwargs: (records[offset:offset+limit], offset+limit < len(records)))
    result, error = server.execute_tool('dy_research_progress',
                                       {'trace_id': 'paged', 'module': 'general-ledger', 'scope_id': 'a'}, tmp_path)
    assert error is None
    assert result['answered'] == 58 and result['route'] == 'interactive'
    assert len(result['unmapped_records']) == 100


def test_multiple_result_sheets_are_not_silently_discarded(tmp_path):
    book = Workbook()
    for sheet, key in [(book.active, 'GL-1-001'), (book.create_sheet('第二批'), 'GL-1-002')]:
        sheet.append(['检查顺序', '配置项', '调研结果'])
        sheet.append([key, '应用', '01｜已配置'])
    path = tmp_path / 'batches.xlsx'
    book.save(path)
    result = sources.parse_source(path, 'general-ledger')
    assert [row['check_order'] for row in result['records']] == ['GL-1-001', 'GL-1-002']
