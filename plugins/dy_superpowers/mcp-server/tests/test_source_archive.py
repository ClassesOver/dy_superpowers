"""全文档案必须隔离、可回查且不会由重试覆盖历史。"""
import json
import sys
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
from dy_superpowers_mcp.source_archive import archive_source


def test_archive_full_text_fixed_path_reuse_and_reparse_history(tmp_path):
    source = tmp_path / '资料.md'
    text = '# 项目\n短\n| 第一列 | 第二列 |\n| --- | --- |\n| 原文 | 内容 |\n' + '完整段落。' * 15000
    source.write_text(text, encoding='utf-8')
    result = archive_source(tmp_path, source, 'expense')
    run = tmp_path / result['path']
    assert run.is_relative_to(tmp_path / '.dy_superpowers-parsed')
    assert result['status'] == 'complete'
    assert text in (run / 'content.md').read_text(encoding='utf-8')
    assert source.read_text(encoding='utf-8') == text
    assert not (tmp_path / '.dy_superpowers').exists()
    assert json.loads((run / 'coverage.json').read_text())['unresolved'] == 0
    assert archive_source(tmp_path, source, 'expense')['path'] == result['path']
    next_run = archive_source(tmp_path, source, 'expense', force=True)
    assert next_run['path'] != result['path'] and (run / 'content.md').exists()


def test_archive_redacts_secrets_and_reports_partial_parse(tmp_path):
    source = tmp_path / '依据.txt'
    source.write_text('password: synthetic-secret\n手机号：13800138000\n政策照常保留', encoding='utf-8')
    result = archive_source(tmp_path, source, 'expense')
    run = tmp_path / result['path']
    all_text = '\n'.join(p.read_text() for p in run.glob('*.json')) + (run / 'content.md').read_text()
    assert 'synthetic-secret' not in all_text and '13800138000' not in all_text
    assert '[REDACTED]' in all_text and '政策照常保留' in all_text
    assert result['redactions'] > 0


def test_candidate_parser_issues_make_archive_partial(tmp_path):
    source = tmp_path / 'QA.json'
    source.write_text('{"items": []}', encoding='utf-8')
    previous = archive_source(tmp_path, source, 'expense', candidates={'issues': []})
    candidates = {'issues': [{'code': 'qa_item_missing_question_or_answer', 'blocking': True}]}

    result = archive_source(tmp_path, source, 'expense', candidates=candidates)

    assert result['path'] != previous['path']
    assert result['status'] == 'partial'
    assert result['coverage']['unresolved'] == 1
    issues = json.loads((tmp_path / result['path'] / 'issues.json').read_text())
    assert any(issue['code'] == 'qa_item_missing_question_or_answer' for issue in issues)
    index = json.loads((tmp_path / '.dy_superpowers-parsed' / result['source_sha256'] / 'manifest.json').read_text())
    assert index['latest_complete'] == previous['path']
    assert index['latest_attempt'] == result['path']


@pytest.mark.parametrize(('issue', 'status', 'unresolved'), [
    ({'code': 'hidden_sheet'}, 'partial', 1),
    ({'code': 'source_note', 'blocking': False}, 'complete', 0),
])
def test_candidate_issue_respects_blocking_flag(tmp_path, issue, status, unresolved):
    source = tmp_path / 'QA.json'
    source.write_text('{"items": []}', encoding='utf-8')
    candidates = {'issues': [issue]}

    result = archive_source(tmp_path, source, 'expense', candidates=candidates)

    assert result['status'] == status
    assert result['coverage']['unresolved'] == unresolved
    saved = json.loads((tmp_path / result['path'] / 'issues.json').read_text())
    assert saved[0]['source_component'] == 'candidate_parser'
    assert saved[0]['code'] == issue['code']
    if status == 'complete':
        assert archive_source(tmp_path, source, 'expense', candidates=candidates)['path'] == result['path']


def test_cli_reports_candidate_problem_once_and_never_marks_it_complete(tmp_path):
    script = Path(__file__).parents[2] / 'skills/module-research/scripts/research_sources.py'
    source = tmp_path / '缺失回答.json'
    source.write_text(json.dumps({'items': [{'question': '适用公司？', 'answer': ''}]},
                                 ensure_ascii=False), encoding='utf-8')
    completed = subprocess.run(
        [sys.executable, str(script), str(source), '--module', 'expense', '--workspace', str(tmp_path)],
        capture_output=True, text=True, timeout=40,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result['status'] == 'partial'
    assert result['issues'] == 1
    saved = json.loads((tmp_path / result['path'] / 'issues.json').read_text())
    assert len(saved) == 1
    assert saved[0]['code'] == 'qa_item_missing_question_or_answer'


def test_archive_failed_retry_keeps_last_complete_and_records_unreadable_object(tmp_path):
    source = tmp_path / '资料.txt'
    source.write_text('确认范围', encoding='utf-8')
    success = archive_source(tmp_path, source, 'expense')

    def failed(_):
        raise ValueError('不要回显文件中的密码')

    result = archive_source(tmp_path, source, 'expense', force=True, extractor=failed)
    assert result['status'] == 'failed'
    manifest = json.loads((tmp_path / '.dy_superpowers-parsed' / result['source_sha256'] / 'manifest.json').read_text())
    assert manifest['latest_complete'] == success['path']
    assert manifest['latest_attempt'] == result['path']
    assert '不要回显' not in (tmp_path / result['path'] / 'issues.json').read_text()


def test_archive_rejects_symlink_root_and_concurrent_reuses(tmp_path):
    source = tmp_path / '资料.txt'
    source.write_text('确认范围', encoding='utf-8')
    with ThreadPoolExecutor(max_workers=3) as pool:
        paths = list(pool.map(lambda _: archive_source(tmp_path, source, 'expense')['path'], range(3)))
    assert len(set(paths)) == 1
    other = tmp_path / 'other'
    other.mkdir()
    (other / '.dy_superpowers-parsed').symlink_to(tmp_path / '.dy_superpowers-parsed', target_is_directory=True)
    with pytest.raises(ValueError, match='符号链接'):
        archive_source(other, source, 'expense')


def test_reuse_checks_generated_file_integrity(tmp_path):
    source = tmp_path / '资料.txt'
    source.write_text('确认范围', encoding='utf-8')
    result = archive_source(tmp_path, source, 'expense')
    (tmp_path / result['path'] / 'content.md').write_text('意外修改', encoding='utf-8')
    with pytest.raises(ValueError, match='摘要'):
        archive_source(tmp_path, source, 'expense')


def test_cli_archives_entire_local_attachment_and_returns_only_paths(tmp_path):
    script = Path(__file__).parents[2] / 'skills/module-research/scripts/research_sources.py'
    attachment = tmp_path / '完整附件.md'
    attachment.write_text('短\n| 第一列 | 第二列 |\n| --- | --- |\n| 全文 | 不得省略 |\n', encoding='utf-8')
    source = tmp_path / '问答.json'
    source.write_text(json.dumps({'items': [{'question': '政策范围？', 'answer': '公司甲',
                                            'attachments': [attachment.name]}]}, ensure_ascii=False), encoding='utf-8')
    command = [sys.executable, str(script), str(source), '--module', 'expense', '--workspace', str(tmp_path)]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=40)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result['status'] == 'complete'
    assert result['attachments'][0]['status'] == 'complete'
    parent = tmp_path / result['path']
    assert json.loads((parent / 'candidates.json').read_text())['records'][0]['conclusion'] == '公司甲'
    child = tmp_path / result['attachments'][0]['path']
    assert '不得省略' in (child / 'content.md').read_text()
    assert '不得省略' not in completed.stdout
    rejected = subprocess.run(command + ['--output', str(tmp_path / 'other.json')], capture_output=True, text=True)
    assert rejected.returncode != 0 and not (tmp_path / 'other.json').exists()
