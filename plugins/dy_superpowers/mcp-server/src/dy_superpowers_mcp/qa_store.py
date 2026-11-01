"""工作区范围内、逐问题并发校验的不可变 QA 版本存储。"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import qa_workbook
from .qa_templates import load_template
from .storage import atomic_write, record_lock
from .trace import redact, workspace_id_for


_ID = re.compile(r'^[a-f0-9]{16}$')
_VERSION = re.compile(r'^[a-f0-9]{32}$')
_PERSONAL = re.compile(r'(?<!\d)(?:1[3-9]\d{9}|\d{17}[\dXx])(?!\d)|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def _safe_text(value, field):
    if not isinstance(value, str):
        raise ValueError(f'{field} 必须是文本')
    if redact(value) != value or _PERSONAL.search(value):
        raise ValueError(f'{field} 包含敏感值，请先脱敏')
    if any(ord(char) < 32 and char not in '\n\r\t' for char in value):
        raise ValueError(f'{field} 包含非法控制字符')
    return value


def _workspace(workspace):
    root = Path(workspace).absolute()
    if not root.is_dir():
        raise ValueError('工作区必须是已有目录')
    return root.resolve()


def _path(root, *parts):
    path = root.joinpath(*parts)
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError('路径超出当前工作区') from exc
    if '..' in relative.parts:
        raise ValueError('路径不得越界')
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('工作区材料和存储路径不得包含符号链接')
    if not path.resolve().is_relative_to(root):
        raise ValueError('路径超出当前工作区')
    return path


def _base(root, module):
    return _path(root, '.dy_superpowers', 'modules', module, 'qa')


@contextmanager
def publication_lock(workspace, module):
    """与 QA 确认共用模块发布锁；调用方在锁内核对依据并发布交付物。"""
    load_template(module)
    root = _workspace(workspace)
    base = _base(root, module)
    base.mkdir(parents=True, exist_ok=True)
    guard = _path(root, str(base.relative_to(root)), 'publish')
    _path(root, str(base.relative_to(root)), 'publish.lock')
    with record_lock(guard):
        yield


def normalize_scope(scope):
    """与保存时相同的范围文本规范化，供读取时比较。"""
    return _scope_text(scope)


def _scope_text(scope):
    text = _safe_text(scope, '适用范围')
    text = re.sub(r'\s*/\s*', '/', unicodedata.normalize('NFKC', text).strip())
    if not text or len(text) > 500:
        raise ValueError('适用范围不能为空或超过500字')
    return text


def _scope_id(value):
    if value is not None and (not isinstance(value, str) or not _ID.fullmatch(value)):
        raise ValueError('范围标识无效')
    return value


def _scope_dir(root, module, sid, revision):
    return _path(root, '.dy_superpowers', 'modules', module, 'qa', 'scopes', sid, revision)


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError('QA 版本或登记文件损坏') from exc


def _envelope(value):
    return {'data': value, 'sha256': _digest(_json(value).encode())}


def _open_envelope(path):
    wrapped = _read_json(path)
    if not isinstance(wrapped, dict) or set(wrapped) != {'data', 'sha256'} or wrapped['sha256'] != _digest(_json(wrapped['data']).encode()):
        raise ValueError('QA 登记摘要校验失败')
    return wrapped['data']


def _metadata(root, template, sid, version, export_id):
    return {'format': 'dy-template-qa-v1', 'workspace_id': workspace_id_for(root),
            'module': template['module'], 'template_revision': template['revision'],
            'scope_id': sid, 'base_version': version, 'export_id': export_id}


def _file_digest(path):
    try:
        return _digest(path.read_bytes())
    except OSError as exc:
        raise ValueError('QA 版本文件缺失或无法读取') from exc


def _snapshot(root, template, sid, pointer=None, version=None):
    directory = _scope_dir(root, template['module'], sid, template['revision'])
    if version is None:
        pointer = _read_json(_path(root, str(directory.relative_to(root)), 'current.json'))
        version = pointer.get('answer_version')
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise ValueError('QA 当前版本标识损坏')
    version_dir = _path(root, str(directory.relative_to(root)), 'versions', version)
    manifest_path = _path(root, str(version_dir.relative_to(root)), 'manifest.json')
    if pointer is not None and pointer.get('manifest_sha256') != _file_digest(manifest_path):
        raise ValueError('QA 当前清单摘要不一致')
    manifest = _open_envelope(manifest_path)
    for name in ('memory.json', 'memory.md', 'answers.xlsx'):
        file = _path(root, str(version_dir.relative_to(root)), name)
        if not file.is_file() or manifest.get('hashes', {}).get(name) != _file_digest(file):
            raise ValueError('QA 版本文件摘要不一致')
    memory = _read_json(version_dir / 'memory.json')
    if (memory.get('answer_version') != version or memory.get('module') != template['module']
            or memory.get('template_revision') != template['revision'] or memory.get('scope_id') != sid):
        raise ValueError('QA 版本身份不一致')
    expected_meta = _metadata(root, template, sid, version, 'version-' + version)
    if manifest.get('metadata') != expected_meta:
        raise ValueError('QA 版本元数据不一致')
    baseline = qa_workbook.verify_snapshot(version_dir / 'answers.xlsx', template, memory['records'], expected_meta)
    if baseline != manifest.get('baseline') or manifest.get('revisions') != {record['question_id']: record['revision'] for record in memory['records']} or manifest.get('completeness') != {record['question_id']: record['research_complete'] for record in memory['records']}:
        raise ValueError('QA 工作簿登记与记忆不一致')
    return memory, version_dir, manifest


def _states(root, template):
    directory = _path(root, str(_base(root, template['module']).relative_to(root)), 'scopes')
    states = []
    if directory.exists():
        for child in sorted(directory.iterdir()):
            _scope_id(child.name)
            current = _path(root, str(child.relative_to(root)), template['revision'], 'current.json')
            if current.exists():
                states.append(_snapshot(root, template, child.name))
    return states


def _progress(records, total):
    confirmed = len(records)
    answered = sum(bool(record['research_complete']) for record in records)
    return {'total': total, 'answered': answered, 'percentage': round(answered / total * 100, 2),
            'confirmed_count': confirmed, 'confirmed_percentage': round(confirmed / total * 100, 2),
            'partial_count': confirmed - answered, 'unknown_count': 0, 'unanswered_count': total - confirmed,
            'route': 'interactive' if answered / total >= .7 else 'excel',
            'progress_summary': f'已确认回答 {confirmed}/{total}；完整完成 {answered}/{total}；部分完成 {confirmed - answered}/{total}；未回答 {total - confirmed}/{total}'}


def _attachment_drift(root, records):
    changes = []
    for record in records:
        for attachment in record['attachments']:
            try:
                file = _attachment_path(root, attachment['reference'])
                actual = _file_digest(file)
            except (ValueError, OSError):
                actual = None
            if actual != attachment['sha256']:
                changes.append({'question_id': record['question_id'], 'reference': attachment['reference'],
                                'expected_sha256': attachment['sha256'], 'actual_sha256': actual,
                                'can_write_confirmed': False})
    return changes


def read_qa(workspace, module, scope_id=None, question_ids=None):
    """纯读取：未知或多范围不拼接答案，不创建任何目录。"""
    template = load_template(module)
    root = _workspace(workspace)
    sid = _scope_id(scope_id)
    questions = template['questions']
    if question_ids is not None:
        if not isinstance(question_ids, list) or any(q not in {point['question_id'] for point in questions} for q in question_ids):
            raise ValueError('问题标识不存在')
        questions = [q for q in questions if q['question_id'] in question_ids]
    states = _states(root, template)
    scopes = [{'scope_id': memory['scope_id'], 'scope': memory['scope'], 'answer_version': memory['answer_version'],
               **_progress(memory['records'], len(template['questions']))} for memory, _, _ in states]
    selected = next((state for state in states if state[0]['scope_id'] == sid), None) if sid else (states[0] if len(states) == 1 else None)
    if sid and selected is None:
        raise ValueError('范围不存在，请使用读取返回的范围标识')
    memory, directory = selected[:2] if selected else ({'records': [], 'answer_version': None, 'scope_id': sid, 'scope': None}, None)
    changes = _attachment_drift(root, memory['records'])
    result = {'module': module, 'template_revision': template['revision'], 'questions': questions,
              'records': [r for r in memory['records'] if question_ids is None or r['question_id'] in question_ids],
              'answer_version': memory['answer_version'], 'scope_id': memory['scope_id'], 'scope': memory['scope'],
              'scope_mode': 'selected' if selected else ('multiple' if len(states) > 1 else 'unscoped'),
              'scopes': scopes, 'scope_progress': scopes,
              'path': str(directory / 'answers.xlsx') if directory else None,
              'attachment_integrity': not changes, 'attachment_changes': changes, **_progress(memory['records'], len(template['questions']))}
    return result


def _attachment_path(root, reference):
    if not isinstance(reference, str) or not reference or Path(reference).is_absolute() or '..' in Path(reference).parts:
        raise ValueError('附件必须引用当前工作区内的相对路径')
    file = _path(root, reference)
    if not file.is_file():
        raise ValueError('附件引用不存在')
    return file


def _attachment_details(values, template, question):
    if not isinstance(values, list):
        raise ValueError('附件 details 必须是列表')
    pages = {page['name']: page['headers'] for page in template['attachments']}
    result, seen = [], set()
    for detail in values:
        if not isinstance(detail, dict) or set(detail) != {'sheet', 'rows'}:
            raise ValueError('附件明细须提供 sheet 和 rows')
        sheet = detail['sheet']
        if not isinstance(sheet, str) or sheet not in question['attachment_names'] or sheet in seen:
            raise ValueError('附件明细只能使用本问题绑定的模板页且不可重复')
        seen.add(sheet)
        if not isinstance(detail['rows'], list):
            raise ValueError('附件明细 rows 必须是列表')
        rows = []
        for row in detail['rows']:
            if not isinstance(row, dict) or not row or set(row) - set(pages[sheet]):
                raise ValueError('附件明细包含未知模板字段或空行')
            normalized = {}
            for field, value in row.items():
                if not (value is None or type(value) in (str, int, float, bool)) or (type(value) is float and not math.isfinite(value)):
                    raise ValueError('附件明细字段必须为有限 JSON 标量，日期时间请使用 ISO 文本')
                if isinstance(value, str):
                    value = _safe_text(value, '附件明细字段')
                    if len(value) > 20000:
                        raise ValueError('附件明细字段超过长度限制')
                normalized[field] = value
            rows.append(normalized)
        result.append({'sheet': sheet, 'rows': rows})
    return result


def _attachments(root, values, template, question, *, verify_files=True):
    if not isinstance(values, list):
        raise ValueError('attachments 必须是列表')
    result, seen = [], set()
    for value in values:
        if not isinstance(value, dict) or not {'reference', 'sha256', 'requirements'} <= set(value) or set(value) - {'reference', 'sha256', 'requirements', 'details'}:
            raise ValueError('附件必须提供引用、摘要和脱敏要求')
        _safe_text(value['reference'], '附件引用')
        reference = value['reference']
        if not reference or Path(reference).is_absolute() or '..' in Path(reference).parts:
            raise ValueError('附件必须引用当前工作区内的相对路径')
        digest = value['sha256']
        if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
            raise ValueError('附件摘要格式无效')
        binding = (reference, digest)
        if binding in seen:
            raise ValueError('同问题附件绑定重复，请合并要求与明细后保存')
        seen.add(binding)
        if verify_files and digest != _file_digest(_attachment_path(root, reference)):
            raise ValueError('附件摘要与真实材料不一致')
        if not isinstance(value['requirements'], list) or not value['requirements']:
            raise ValueError('附件需要已确认的结构化要求')
        requirements = [_safe_text(text, '附件要求').strip() for text in value['requirements']]
        if not all(requirements):
            raise ValueError('附件要求不能为空')
        normalized = {'reference': value['reference'], 'sha256': digest, 'requirements': requirements}
        if 'details' in value:
            normalized['details'] = _attachment_details(value['details'], template, question)
        result.append(normalized)
    return result


def _validate_answers(root, template, answers, *, verify_attachments=True):
    if not isinstance(answers, list):
        raise ValueError('answers 必须是列表')
    seen = set()
    validated = []
    for item in answers:
        if not isinstance(item, dict) or not {'question_id', 'answer', 'research_complete', 'expected_revision'} <= set(item) or set(item) - {'question_id', 'answer', 'research_complete', 'expected_revision', 'attachments'}:
            raise ValueError('答案字段无效或缺少逐问题版本')
        qid = item['question_id']
        if qid not in {q['question_id'] for q in template['questions']} or qid in seen:
            raise ValueError('问题不存在或同批重复')
        seen.add(qid)
        if type(item['research_complete']) is not bool:
            raise ValueError('research_complete 必须是布尔值')
        if item['expected_revision'] is not None and not isinstance(item['expected_revision'], str):
            raise ValueError('expected_revision 必须是读取值或 null')
        value = {**item, 'answer': _safe_text(item['answer'], '答案').strip()}
        if len(value['answer']) > 20000:
            raise ValueError('答案超过长度限制')
        if 'attachments' in item:
            value['attachments'] = _attachments(root, item['attachments'], template, next(q for q in template['questions'] if q['question_id'] == qid), verify_files=verify_attachments)
        validated.append(value)
    return validated


def _markdown(memory):
    lines = [f'# {memory["module"]} 调研确认记忆', f'范围：{memory["scope"]}',
             f'模板修订：{memory["template_revision"]}', f'答案版本：{memory["answer_version"]}', '']
    for record in memory['records']:
        lines.extend([f'## {record["question_id"]}', record['answer'],
                      f'调研状态：{"已完成" if record["research_complete"] else "部分完成"}',
                      f'确认人：{record["confirmed_by"]}；确认时间：{record["confirmed_at"]}', ''])
        for attachment in record['attachments']:
            lines.extend([f'- 附件：{attachment["reference"]}（{attachment["sha256"]}）',
                          *[f'  - {text}' for text in attachment['requirements']]])
    return '\n'.join(lines) + '\n'


def commit_qa(workspace, module, *, scope, scope_id=None, template_revision, answers, batch_id, confirmed, confirmed_by='未提供'):
    """完整校验后发布一版；仅同问题修订冲突会阻止并发合并。"""
    if confirmed is not True:
        raise ValueError('保存 QA 需要明确确认')
    template = load_template(module)
    if template_revision != template['revision']:
        raise ValueError('模板修订不匹配')
    root = _workspace(workspace)
    scope = _scope_text(scope)
    confirmed_by = _safe_text(confirmed_by, '确认人').strip() or '未提供'
    sid = _scope_id(scope_id) or _digest(scope.encode())[:16]
    if not isinstance(batch_id, str) or not batch_id.strip():
        raise ValueError('批次标识不能为空')
    _safe_text(batch_id, '批次标识')
    values = _validate_answers(root, template, answers, verify_attachments=False)
    payload_hash = _digest(_json({'scope_id': sid, 'scope': scope, 'template_revision': template_revision,
                                 'answers': values, 'confirmed_by': confirmed_by}).encode())
    base = _base(root, module)
    with publication_lock(root, module):
        states = _states(root, template)
        current = next((state for state in states if state[0]['scope_id'] == sid), None)
        if current and current[0]['scope'] != scope:
            raise ValueError('范围文本与范围标识不匹配')
        if scope_id and current is None:
            raise ValueError('范围标识不存在，请沿用已登记范围')
        batch_key = _digest(batch_id.encode())
        for memory, _, _ in states:
            receipt = memory.get('batches', {}).get(batch_key)
            if receipt is not None:
                if receipt['payload_hash'] != payload_hash:
                    raise ValueError('同一批次标识不能提交不同内容')
                result = read_qa(root, module, sid if current else None)
                return {**result, 'stored': False, 'idempotent': True}
        # 无业务变化的批次只登记收据，不制造答案版本。
        batch_path = _path(root, str(base.relative_to(root)), 'batches', batch_key + '.json')
        if batch_path.exists():
            batch = _open_envelope(batch_path)
            if batch['payload_hash'] != payload_hash:
                raise ValueError('同一批次标识不能提交不同内容')
            return {**read_qa(root, module, sid if current else None), 'stored': False, 'idempotent': True}
        # 收据匹配前仅规范并检查请求安全；新批次才重新验证材料现状。
        values = _validate_answers(root, template, values)
        prior = {r['question_id']: r for r in current[0]['records']} if current else {}
        for value in values:
            if not value['answer'] and 'attachments' not in value:
                continue
            if not value['answer'] and value['question_id'] not in prior:
                raise ValueError(f'问题 {value["question_id"]} 尚无已确认答案，不能只提交附件；请同时提供答案')
            expected = prior.get(value['question_id'], {}).get('revision')
            if value['expected_revision'] != expected:
                raise ValueError(f'问题 {value["question_id"]} 修订冲突，请重新读取并确认')
        merged = dict(prior)
        now = datetime.now(timezone.utc).isoformat()
        for value in values:
            old = prior.get(value['question_id'])
            if not value['answer'] and 'attachments' not in value:
                continue
            if not value['answer'] and old is None:
                continue
            core = {'question_id': value['question_id'], 'answer': value['answer'] or old['answer'],
                    'research_complete': value['research_complete'] if value['answer'] else old['research_complete'],
                    'attachments': value.get('attachments', old['attachments'] if old else [])}
            if old and 'attachments' in value:
                previous_bindings = {(bound['reference'], bound['sha256']): bound for bound in old['attachments']}
                core['attachments'] = [{**bound, 'details': previous_bindings[(bound['reference'], bound['sha256'])]['details']}
                    if 'details' not in bound and 'details' in previous_bindings.get((bound['reference'], bound['sha256']), {})
                    else bound for bound in core['attachments']]
            if old and all(old[key] == field for key, field in core.items()):
                continue
            merged[value['question_id']] = {**core, 'confirmed_by': confirmed_by, 'confirmed_at': now,
                                           'revision': _digest(_json({'previous': old['revision'] if old else None, **core}).encode())}
        changed = merged != prior
        if changed:
            version = uuid.uuid4().hex
            directory = _scope_dir(root, module, sid, template_revision)
            version_dir = _path(root, str(directory.relative_to(root)), 'versions', version)
            version_dir.mkdir(parents=True)
            ordered = [merged[q['question_id']] for q in template['questions'] if q['question_id'] in merged]
            memory = {'module': module, 'template_revision': template_revision, 'scope': scope, 'scope_id': sid,
                      'answer_version': version, 'records': ordered,
                      'batches': {**(current[0].get('batches', {}) if current else {}),
                                  batch_key: {'batch_id': batch_id, 'payload_hash': payload_hash}}}
            atomic_write(version_dir / 'memory.json', _json(memory))
            atomic_write(version_dir / 'memory.md', _markdown(memory))
            metadata = _metadata(root, template, sid, version, 'version-' + version)
            baseline = qa_workbook.build_workbook(version_dir / 'answers.xlsx', template, ordered, metadata)
            qa_workbook.verify_snapshot(version_dir / 'answers.xlsx', template, ordered, metadata)
            manifest = {'metadata': metadata, 'baseline': baseline,
                        'scope': scope, 'revisions': {r['question_id']: r['revision'] for r in ordered},
                        'completeness': {r['question_id']: r['research_complete'] for r in ordered},
                        'hashes': {name: _digest((version_dir / name).read_bytes()) for name in ('memory.json', 'memory.md', 'answers.xlsx')}}
            atomic_write(version_dir / 'manifest.json', _json(_envelope(manifest)))
            _snapshot(root, template, sid, version=version)
            pointer = {'answer_version': version, 'manifest_sha256': _digest((version_dir / 'manifest.json').read_bytes())}
            atomic_write(directory / 'current.json', _json(pointer))
        else:
            batch_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(batch_path, _json(_envelope({'payload_hash': payload_hash})))
        result = read_qa(root, module, sid if changed or current else None)
        return {**result, 'stored': changed, 'idempotent': False}


def export_qa(workspace, module, *, scope_id=None, confirmed):
    """授权后创建新下载文件；每个范围分别导出，文件名永不覆盖。"""
    if confirmed is not True:
        raise ValueError('导出答卷需要明确确认')
    root = _workspace(workspace)
    template = load_template(module)
    state = read_qa(root, module, scope_id)
    if state['scope_mode'] == 'multiple':
        return {'module': module, 'scope_mode': 'multiple', 'files': [export_qa(root, module, scope_id=s['scope_id'], confirmed=True) for s in state['scopes']]}
    selected_scope_id = state['scope_id']
    base = _base(root, module)
    with publication_lock(root, module):
        state = read_qa(root, module, selected_scope_id)
        if state['scope_mode'] != 'multiple':
            directory = _path(root, '.dy_superpowers', 'research', module)
            directory.mkdir(parents=True, exist_ok=True)
            day = datetime.now().strftime('%Y%m%d')
            serial = 1
            while True:
                path = _path(root, str(directory.relative_to(root)), f'{template["title"]}{day}{serial:03d}.xlsx')
                if not path.exists():
                    break
                serial += 1
            export_id = uuid.uuid4().hex
            metadata = _metadata(root, template, state['scope_id'], state['answer_version'], export_id)
            baseline = qa_workbook.build_workbook(path, template, state['records'], metadata)
            qa_workbook.verify_snapshot(path, template, state['records'], metadata)
            registration = {'metadata': metadata, 'baseline': baseline,
                            'scope': state['scope'], 'revisions': {r['question_id']: r['revision'] for r in state['records']},
                            'completeness': {r['question_id']: r['research_complete'] for r in state['records']}}
            registry = _path(root, str(base.relative_to(root)), 'exports', export_id + '.json')
            registry.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(registry, _json(_envelope(registration)))
            return {'module': module, 'path': str(path), 'template_revision': template['revision'],
                    'scope_id': state['scope_id'], 'scope': state['scope'], 'answer_version': state['answer_version'], 'question_count': len(template['questions'])}
    return {'module': module, 'scope_mode': 'multiple',
            'files': [export_qa(root, module, scope_id=entry['scope_id'], confirmed=True) for entry in state['scopes']]}


def parse_qa_workbook(workspace, path):
    """工作区内登记过的答卷可复制回填，解析永不确认或写档案。"""
    root = _workspace(workspace)
    candidate = Path(path)
    file = _path(root, str(candidate.relative_to(root)) if candidate.is_absolute() and candidate.is_relative_to(root) else str(candidate))
    if not file.is_file():
        raise ValueError('答卷必须是当前工作区内已有文件')
    book, metadata = qa_workbook.metadata_from(file)
    try:
        template = load_template(metadata['module'])
    except (KeyError, TypeError) as exc:
        raise ValueError('未知答卷模板') from exc
    if metadata.get('format') != 'dy-template-qa-v1' or metadata.get('workspace_id') != workspace_id_for(root) or metadata.get('template_revision') != template['revision']:
        raise ValueError('答卷模板或工作区元数据不匹配')
    sid = _scope_id(metadata.get('scope_id'))
    export_id = metadata.get('export_id')
    if isinstance(export_id, str) and export_id.startswith('version-'):
        version = metadata.get('base_version')
        if not sid or export_id != 'version-' + str(version):
            raise ValueError('版本答卷元数据无效')
        _, _, registration = _snapshot(root, template, sid, version=version)
    elif isinstance(export_id, str) and _VERSION.fullmatch(export_id):
        registry = _path(root, str(_base(root, template['module']).relative_to(root)), 'exports', export_id + '.json')
        registration = _open_envelope(registry)
    else:
        raise ValueError('答卷未登记')
    if registration['metadata'] != metadata:
        raise ValueError('答卷元数据被伪造或修改')
    answers, changes = qa_workbook.extract_candidates(book, template, registration)
    for answer in answers:
        _safe_text(answer['answer'], '答卷答案')
    for change in changes:
        if isinstance(change['value'], str):
            _safe_text(change['value'], '附件候选')
    return {'module': template['module'], 'template_revision': template['revision'], 'scope_id': sid,
            'scope': registration.get('scope'), 'base_version': metadata['base_version'], 'answers': answers, 'attachment_changes': changes,
            'can_write_confirmed': False}
