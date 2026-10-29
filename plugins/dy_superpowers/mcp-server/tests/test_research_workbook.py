"""调研清单导出、回填与再导入；只操作临时工作区。"""
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import re
import sys

import pytest
from openpyxl import Workbook, load_workbook

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
from dy_superpowers_mcp.research_progress import _guide_items, export_unanswered_template
from dy_superpowers_mcp.source_archive import archive_source

SCRIPT = Path(__file__).parents[2] / 'skills/module-research/scripts/research_sources.py'
spec = importlib.util.spec_from_file_location('workbook_research_sources', SCRIPT)
sources = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sources)

HEADERS = ['模块', '检查顺序', '配置项', '调研子项', '配置子项', '调研问题',
           '回答指引与提取要素', '已记忆项', '当前状态', '答案', '附件/补充材料']


def _export(workspace, module='general-ledger', records=(), **kwargs):
    return export_unanswered_template(workspace, module, 'workbook-test', records=list(records),
                                      confirmed=True, **kwargs)


def _record(module='general-ledger', **kwargs):
    item = _guide_items(module)[0]
    return dict(module=module, check_order=item['check_order'], research_id=item['research_id'],
                config_item=item['config_item'], conclusion='本期使用总账基础及凭证应用',
                status='confirmed', research_complete=True, scope='甲院2026年', scope_id='site-a',
                revision='saved-revision', **kwargs)


@pytest.mark.parametrize('module,label,total,first_group_range,first_subitem', [
    ('expense', '费用', 92, 'A2:A7', '费用模块基础应用'),
    ('general-ledger', '总账', 82, 'A2:A4', '总账基础及凭证应用'),
])
def test_initial_workbook_uses_guide_keys_and_shared_question_layout(tmp_path, module, label, total,
                                                                    first_group_range, first_subitem):
    result = _export(tmp_path, module)
    path = tmp_path / result['path']
    assert re.fullmatch(label + r'调研清单\d{8}01\.xlsx', path.name)
    with closing(load_workbook(path)) as book:
        assert book.sheetnames[0] == '调研问题清单'
        if module == 'expense':
            assert book.sheetnames[1] == '01_应用与主体'
        else:
            assert book.sheetnames[1] == '公司应用清单'
            assert [c.value for c in book['公司应用清单'][1] if c.value is not None] == [
                '公司编码 *', '公司名称 *', '应用类别 *', '应用名称 *', '依赖应用']
        assert len(book.sheetnames) > 1
        sheet = book.active
        assert [cell.value for cell in sheet[1]] == HEADERS
        assert sheet.max_row == total + 1
        assert all(row[9] is None for row in sheet.iter_rows(min_row=2, values_only=True))
        assert sheet['C2'].value == '应用安装'
        assert sheet['B2'].value == ('EXP-001' if module == 'expense' else 'GL-1-001')
        assert sheet['D2'].value == '01'
        assert sheet['E2'].value == first_subitem
        assert sheet['F2'].value
        assert sheet['A1'].fill.fgColor.rgb == 'FF2F6B5F'
        assert sheet['A1'].font.color.type == 'theme' and sheet['A1'].font.color.theme == 0
        assert sheet['E2'].font.name == '宋体'
        assert sheet.row_dimensions[2].height == 88
        assert sheet['F2'].alignment.vertical == 'center'
        assert first_group_range in {str(area) for area in sheet.merged_cells.ranges}
        for detail in book.worksheets[1:]:
            assert detail.sheet_state == 'visible'
            assert detail.max_row >= 2
            assert any(str(cell.value or '').startswith('示例') for cell in detail[2])
    parsed = sources.parse_source(path, module)
    assert parsed['records'] == []
    assert len(parsed['unanswered']) == total
    assert parsed['unanswered'][0]['check_order'] == ('1' if module == 'expense' else 'GL-1-001')
    assert parsed['unanswered'][0]['research_id'] == '01'


@pytest.mark.parametrize('module', ['expense', 'general-ledger'])
def test_export_order_and_subitems_match_every_guide_mapping_row(tmp_path, module):
    guide = SCRIPT.parents[1] / 'references' / f'{module}-research-guide.md'
    text = guide.read_text()
    qa = text.split('## 模块 QA\n', 1)[1].split('\n### ', 1)[0].split('\n## ', 1)[0]
    groups = {}
    for line in qa.splitlines():
        if line.startswith(('| EXP-', '| GL-')):
            cells = [cell.strip() for cell in line.strip('|').split('|')]
            groups[cells[0]] = (cells[1], cells[3])
    if module == 'general-ledger':
        supplement = text.split('### 补充 QA\n', 1)[1].split('\n## ', 1)[0]
        row = next(line for line in supplement.splitlines() if line.startswith('| general-ledger |'))
        cells = [cell.strip() for cell in row.strip('|').split('|')]
        groups[cells[1]] = ('真实辅助组合', cells[2])
    mapping = text.split('## 提取要素与矩阵映射\n', 1)[1].split('\n## ', 1)[0]
    expected = []
    for line in mapping.splitlines():
        if line.startswith(f'| {module} |'):
            _, order, subitem, extraction, *_ = [cell.strip() for cell in line.strip('|').split('|')]
            expected.append((order, subitem, extraction.split('：', 1)[0], extraction))
    result = _export(tmp_path, module)
    with closing(load_workbook(tmp_path / result['path'])) as book:
        sheet = book.active
        group_order = group_name = group_question = None
        actual = []
        for row in sheet.iter_rows(min_row=2, values_only=True):
            group_order = row[1] or group_order
            group_name = row[2] or group_name
            group_question = row[5] or group_question
            assert (group_name, group_question) == groups[group_order]
            actual.append((group_order, row[3], row[4], row[6]))
        assert actual == expected


def test_repeated_and_concurrent_exports_get_new_daily_serials_without_overwrite(tmp_path):
    first = _export(tmp_path)
    original = (tmp_path / first['path']).read_bytes()
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: _export(tmp_path), range(3)))
    names = [Path(result['path']).name for result in results]
    assert len(set(names)) == 3
    prefix = Path(first['path']).stem[:-2]
    assert set(names) == {f'{prefix}{number:02d}.xlsx' for number in (2, 3, 4)}
    assert (tmp_path / first['path']).read_bytes() == original


def test_export_keeps_full_question_set_and_all_current_memory_answers(tmp_path):
    complete = _record()
    partial = {**complete, 'research_id': '02', 'research_complete': False,
               'conclusion': '仅确认本期需要扩展，清单待补充'}
    retired = {**complete, 'research_id': '03', 'status': 'retired', 'conclusion': '旧结论'}
    result = _export(tmp_path, records=[complete, partial, retired])
    with closing(load_workbook(tmp_path / result['path'])) as book:
        assert book.active.max_row == 83
        assert book.active['H2'].value.endswith(complete['conclusion'])
        assert book.active['I2'].value == '已确认'
        assert book.active['H3'].value.endswith(partial['conclusion'])
        assert book.active['I3'].value == '已有局部确认事实；完整性待补充'
        assert book.active['H4'].value is None
    parsed = sources.parse_source(tmp_path / result['path'], 'general-ledger')
    assert len(parsed['records']) == 2
    assert parsed['records'][0]['scope_id'] == 'site-a'
    assert parsed['records'][0]['scope'] == '甲院2026年'
    assert parsed['records'][1]['research_complete'] is False
    assert all(record['can_write_confirmed'] is False for record in parsed['records'])


def test_multiple_scopes_export_separate_answers_without_merging_them(tmp_path):
    first = _record()
    second = {**first, 'scope_id': 'site-b', 'scope': '乙院2027年', 'conclusion': '后期交付'}
    result = _export(tmp_path, records=[first, second])
    parsed = sources.parse_source(tmp_path / result['path'], 'general-ledger')
    assert {(record['scope_id'], record['scope'], record['conclusion']) for record in parsed['records']} == {
        ('site-a', '甲院2026年', first['conclusion']), ('site-b', '乙院2027年', '后期交付')}
    assert len(parsed['records']) == 2


def test_multiline_memory_and_identical_scope_labels_keep_separate_records(tmp_path):
    first = {**_record(), 'conclusion': '第一条规则\n第二条规则'}
    second = {**first, 'scope_id': 'site-b', 'research_complete': False, 'conclusion': '另一批局部规则'}
    result = _export(tmp_path, records=[first, second])
    parsed = sources.parse_source(tmp_path / result['path'], 'general-ledger')
    assert {(r['scope_id'], r['conclusion'], r['research_complete']) for r in parsed['records']} == {
        ('site-a', first['conclusion'], True), ('site-b', second['conclusion'], False)}


def test_filled_workbook_import_uses_current_cells_instead_of_old_memory(tmp_path):
    result = _export(tmp_path, records=[_record()])
    path = tmp_path / result['path']
    book = load_workbook(path)
    book.active['H2'] = None
    book.active['J3'] = '本期不启用其他扩展'
    book.active['K3'] = '公司应用清单'
    book.save(path)
    book.close()
    parsed = sources.parse_source(path, 'general-ledger')
    assert len(parsed['records']) == 1
    answer = parsed['records'][0]
    assert answer['research_id'] == '02'
    assert answer['conclusion'] == '本期不启用其他扩展'
    assert answer['config_item'] == '应用安装'
    assert answer['config_subitem'] == '其他扩展应用'
    assert answer['research_question']
    assert answer['attachment_materials'] == '公司应用清单'
    assert answer['can_write_confirmed'] is False
    assert any(row['research_id'] == '01' for row in parsed['unanswered'])


def test_saved_attachment_tabs_are_reexported_and_latest_checklist_is_authoritative(tmp_path):
    first = _export(tmp_path)
    source = tmp_path / first['path']
    book = load_workbook(source)
    sheet = book['公司应用清单']
    sheet.delete_rows(2)
    sheet.append(['DEMO', '仅测试单位', '总账', '凭证管理', None])
    extra = book.create_sheet('本期补充附件')
    extra.append(['业务类型', '要求'])
    extra.append(['期末业务', '先复核'])
    book.active['J2'] = '仅测试单位使用凭证管理'
    book.save(source)
    book.close()
    archive_source(tmp_path, source, 'general-ledger', candidates=sources.parse_source(source, 'general-ledger'))
    second = _export(tmp_path, records=[_record()])
    current = tmp_path / second['path']
    with closing(load_workbook(current)) as book:
        assert book['公司应用清单']['A2'].value == 'DEMO'
        assert book['公司应用清单']['D2'].value == '凭证管理'
        assert book['公司应用清单'].max_row == 2
        assert book['本期补充附件']['B2'].value == '先复核'
        guide = SCRIPT.parents[1] / 'references/general-ledger-research-guide.md'
        expected_tabs = re.findall(r'^### 模板：(.+)$', guide.read_text(), re.M)
        assert book.sheetnames == ['调研问题清单', *expected_tabs, '本期补充附件']
    book = load_workbook(current)
    book['公司应用清单'].delete_rows(2)
    book.save(current)
    book.close()
    archive_source(tmp_path, current, 'general-ledger', candidates=sources.parse_source(current, 'general-ledger'))
    third = _export(tmp_path)
    with closing(load_workbook(tmp_path / third['path'])) as book:
        assert book['公司应用清单'].max_row == 1, '当前文件已清空附件，不得复活旧行或示例'


def test_old_layout_is_still_readable_without_requiring_deleted_columns(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.append(['模块', '检查顺序', '调研子项', '配置项', '待补充问题与提取要素',
                  '适用公司/院区', '适用业务范围', '上线阶段/期间', '答案', '依据/来源', '附件/补充材料'])
    sheet.append(['general-ledger', 'GL-1-001', '01', '应用', '问题', '甲院', '总账',
                  '2026年', '本期启用', '业务访谈', '附件一'])
    path = tmp_path / '旧格式.xlsx'
    book.save(path)
    book.close()
    record = sources.parse_source(path, 'general-ledger')['records'][0]
    assert record['scope_fields']['适用公司/院区'] == '甲院'
    assert record['suggested_evidence'] == '业务访谈'
    assert record['attachment_materials'] == '附件一'
