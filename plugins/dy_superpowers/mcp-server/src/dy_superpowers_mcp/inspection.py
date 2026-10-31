"""校验声明范围内的巡检记录，不代替业务证据真实性判断。"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from .qa_store import read_qa
from .trace import TraceStore, TRACE_CODE_PATTERN
from .qa_basis import attachment_detail_checks, configured_environment_info, optional_attachment

KEY_FIELDS = ("module", "check_order", "research_id", "scope_id", "environment")
RESULTS = {"通过", "部分通过", "不通过", "不适用", "待确认", "未检查", "未知"}
DEFINITIVE = {"通过", "部分通过", "不通过", "不适用"}


def _aggregate(results):
    if '不通过' in results:
        return '不通过'
    if not results or any(value not in {'通过', '不适用'} for value in results):
        return '待确认'
    return '不适用' if all(value == '不适用' for value in results) else '通过'


def _time(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed if parsed.tzinfo is not None and parsed <= datetime.now(timezone.utc) else None
    except (ValueError, AttributeError, TypeError):
        return None


def _file_evidence(workspace, evidence):
    reference, sha = evidence.get('reference'), evidence.get('sha256')
    if not isinstance(reference, str) or not reference or not isinstance(sha, str):
        return False
    root = Path(workspace).resolve()
    target = root / reference
    try:
        relative = target.relative_to(root)
        current = root
        for part in relative.parts:
            current = current / part
            if part in {'.', '..'} or current.is_symlink():
                return False
        return target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == sha
    except (OSError, ValueError):
        return False


def validate_inspection(workspace: Path, report: object) -> dict:
    errors = []
    def error(path, message):
        errors.append({'path': path, 'message': message})
    if not isinstance(report, dict):
        report = {}
    basis = report.get('basis')
    state = None
    if not isinstance(basis, dict) or any(not isinstance(basis.get(k), str) or not basis[k].strip()
            for k in ('module', 'scope_id', 'environment', 'template_revision', 'answer_version')):
        error('basis', '必须提供当前模块、范围、环境、模板修订和答案版本；旧报告仅可历史读取')
        basis = basis if isinstance(basis, dict) else {}
    else:
        if not TRACE_CODE_PATTERN.fullmatch(basis['environment']) or len(basis['environment']) > 128:
            error('basis.environment', '环境必须是稳定标识')
        identity = configured_environment_info()
        configured = identity['environment']
        if basis.get('connection_fingerprint') != identity['connection_fingerprint']:
            error('basis.connection_fingerprint', '连接身份已变化或缺失，请重新准备巡检')
        if configured is not None and configured != basis['environment']:
            error('basis.environment', '报告环境与当前服务实际配置身份不一致')
        try:
            state = read_qa(workspace, basis['module'], basis['scope_id'])
            if any(basis[k] != state[k] for k in ('module', 'scope_id', 'template_revision', 'answer_version')):
                error('basis', '模板或答案版本已变化，请重新准备巡检')
            if not state['attachment_integrity']:
                error('basis', '绑定附件摘要已变化，请重新确认并准备巡检')
        except (ValueError, OSError) as exc:
            error('basis', '当前 QA 不可用：' + str(exc))
    questions = {q['question_id']: q for q in state['questions']} if state else {}
    answers = {r['question_id']: r for r in state['records']} if state else {}
    research_digests = {a['sha256'] for answer in answers.values() for a in answer.get('attachments', [])}
    expected = report.get('expected_checks')
    expected_ids = []
    if not isinstance(expected, list):
        error('expected_checks', '必须覆盖当前模板所有问题')
    else:
        for i, item in enumerate(expected):
            if not isinstance(item, dict) or not isinstance(item.get('question_id'), str):
                error(f'expected_checks[{i}]', '必须提供 question_id')
                continue
            if any(k in item and item[k] != basis.get(k) for k in ('module', 'scope_id', 'environment')):
                error(f'expected_checks[{i}]', '检查项不能跨模块、范围或环境')
            expected_ids.append(item['question_id'])
        if not expected_ids or len(expected_ids) != len(set(expected_ids)) or set(expected_ids) != set(questions):
            error('expected_checks', '必须完整且不重复覆盖模板全部问题，不能删除问题凑通过')
    records = report.get('records')
    if not isinstance(records, list):
        error('records', '巡检记录必须是列表')
        records = []
    seen, outcomes = set(), []
    for index, record in enumerate(records):
        path = f'records[{index}]'
        if not isinstance(record, dict):
            error(path, '记录必须是对象')
            continue
        qid = record.get('question_id')
        if not isinstance(qid, str) or qid not in questions or qid in seen:
            error(path, '问题重复或不属于本模块当前模板')
            continue
        seen.add(qid)
        if any(k in record and record[k] != basis.get(k) for k in ('module', 'scope_id', 'environment')):
            error(path, '记录不能跨模块、范围或环境')
        answer = answers.get(qid)
        result = record.get('result')
        if not isinstance(result, str) or result not in RESULTS:
            error(path + '.result', '结果状态不合法')
        else:
            outcomes.append(result)
        for field in ('verification_method', 'reason'):
            if not isinstance(record.get(field), str) or not record[field].strip():
                error(path + '.' + field, '必须提供非空文本')
        verified = _time(record.get('verified_at'))
        if verified is None:
            error(path + '.verified_at', '验证时间必须包含时区且不晚于当前')
        if record.get('answer_revision') != (answer['revision'] if answer else None):
            error(path + '.answer_revision', '答案修订不匹配，须重新验证')
        if isinstance(result, str) and result in DEFINITIVE:
            if not answer or answer.get('research_complete') is not True:
                error(path, '明确结果需要同范围完整确认答案')
            if answer and verified and verified <= _time(answer['confirmed_at']):
                error(path + '.verified_at', '验证时间必须晚于答案确认时间')
        requirements = record.get('requirements')
        if not isinstance(requirements, list):
            error(path + '.requirements', '业务要求必须是列表')
            requirements = []
        if isinstance(result, str) and result in DEFINITIVE and not requirements:
            error(path + '.requirements', '明确结果必须提供非空业务要求')
        optional_unused = (optional_attachment(questions[qid]) and result == '不适用' and bool(requirements)
                           and all(isinstance(req, dict) and req.get('result') == '不适用' for req in requirements))
        if isinstance(result, str) and result in DEFINITIVE and questions[qid].get('attachment_names') and not optional_unused and (not answer or not answer.get('attachments')):
            error(path, '缺少确认附件要求，应标待确认')
        attachments = {(a['reference'], a['sha256']): a for a in answer.get('attachments', [])} if answer else {}
        if answer and len(attachments) != len(answer.get('attachments', [])):
            error(path, '当前答案含重复附件绑定，须合并确认后重新巡检')
        detail_checks = attachment_detail_checks(list(attachments.values()))
        covered, detail_covered, requirement_ids, requirement_results = set(), set(), set(), []

        def evidence_valid(evidence, requirement_id=None, comparison_result=None):
            if not isinstance(evidence, dict) or not isinstance(evidence.get('reference'), str) or not evidence['reference'].strip():
                return False
            kind = evidence.get('kind')
            if isinstance(kind, str) and kind in {'document', 'manual'}:
                valid = _file_evidence(workspace, evidence)
                if not valid or evidence['sha256'] in research_digests:
                    return False
                if kind == 'document':
                    observed = _time(evidence.get('observed_at'))
                    valid = valid and answer is not None and observed is not None and observed > _time(answer['confirmed_at']) and verified is not None and observed <= verified
                if kind == 'manual':
                    signed = _time(evidence.get('signed_at'))
                    valid = valid and isinstance(evidence.get('signed_by'), str) and bool(evidence['signed_by'].strip()) and signed is not None
                    valid = valid and answer is not None and signed > _time(answer['confirmed_at']) and verified is not None and verified >= signed
                return valid
            if kind != 'odoo' or not isinstance(evidence.get('trace_id'), str) or not isinstance(evidence.get('model'), str):
                return False
            identity = configured_environment_info()
            fingerprint = basis.get('connection_fingerprint')
            if not identity['configured'] or not isinstance(fingerprint, str) or len(fingerprint) != 64 or fingerprint != identity['connection_fingerprint']:
                return False
            try:
                events = TraceStore(workspace).read(evidence['trace_id'])
            except (ValueError, OSError):
                return False
            completed = {(e.get('span_id'), e.get('tool')) for e in events if e.get('event') == 'tool.end' and e.get('status') == 'ok'}
            context = {k: basis[k] for k in ('module', 'scope_id', 'environment', 'template_revision', 'answer_version')}
            if basis.get('connection_fingerprint'):
                context['connection_fingerprint'] = basis['connection_fingerprint']
            context.update(question_id=qid, answer_revision=record.get('answer_revision'))
            matching = [e for e in events if e.get('event') == 'odoo.read' and e.get('status') == 'ok'
                and e.get('tool') in {'dy_odoo_record_search', 'dy_odoo_record_read', 'dy_odoo_module_info', 'dy_odoo_module_list_installed', 'dy_odoo_module_list_available', 'dy_odoo_model_list', 'dy_odoo_model_search_field'}
                and e.get('operation') in {'search_read', 'record_read', 'module_info', 'module_list_installed'}
                and e.get('model') == evidence['model'] and (e.get('span_id'), e.get('tool')) in completed
                and isinstance(e.get('trace_context'), dict)
                and all(e['trace_context'].get(k) == v for k, v in context.items())
                and (not e['trace_context'].get('requirement_id')
                     or e['trace_context']['requirement_id'] == requirement_id
                     or (requirement_id is None and e['trace_context']['requirement_id'] in requirement_ids))
                and _time(e.get('timestamp')) is not None and answer is not None
                and _time(e['timestamp']) > _time(answer['confirmed_at'])
                and verified is not None and verified >= _time(e['timestamp'])]
            if comparison_result is None:
                return bool(matching)
            from .odoo_comparison import comparison_digest
            try:
                digest = comparison_digest(evidence.get('comparison'))
            except ValueError:
                return False
            if not matching:
                return False
            latest = max(matching, key=lambda e: _time(e['timestamp']))
            expected_status = 'passed' if comparison_result in {'通过', '不适用'} else 'mismatch'
            return latest.get('comparison_digest') == digest and latest.get('comparison_status') == expected_status

        for number, requirement in enumerate(requirements):
            req_path = f'{path}.requirements[{number}]'
            if not isinstance(requirement, dict):
                error(req_path, '要求必须是对象')
                continue
            rid = requirement.get('requirement_id')
            if not isinstance(rid, str) or not rid.strip() or rid in requirement_ids:
                error(req_path, '要求标识必须非空且唯一')
            requirement_ids.add(str(rid))
            text = requirement.get('text')
            if not isinstance(text, str) or not text.strip():
                error(req_path, '业务要求必须提供非空文本')
            req_result = requirement.get('result')
            if not isinstance(req_result, str) or req_result not in RESULTS:
                error(req_path, '要求结果状态不合法')
            else:
                requirement_results.append(req_result)
            if isinstance(req_result, str) and req_result in DEFINITIVE:
                if not isinstance(requirement.get('verification_target'), str) or requirement['verification_target'] not in {'configuration', 'business', 'document'}:
                    error(req_path, '明确要求结果必须声明核验目标：configuration、business 或 document')
                if not answer or not answer.get('research_complete'):
                    error(req_path, '明确要求结果需要完整确认答案')
                if answer and verified and verified <= _time(answer['confirmed_at']):
                    error(req_path, '要求核验必须晚于确认')
            sources = requirement.get('source_refs')
            if not isinstance(sources, list) or not sources:
                error(req_path, '要求必须引用本问题当前确认答案或绑定附件')
                sources = []
            for source in sources:
                if not isinstance(source, dict):
                    error(req_path, '来源引用必须是对象')
                elif source.get('kind') == 'answer':
                    if not answer or source.get('question_id') != qid or source.get('revision') != answer['revision']:
                        error(req_path, '答案来源不是同问题当前确认修订')
                elif source.get('kind') == 'attachment':
                    attachment = attachments.get((source.get('reference'), source.get('sha256'))) if all(isinstance(source.get(k), str) for k in ('reference', 'sha256')) else None
                    if attachment is None:
                        error(req_path, '附件来源不是同问题当前确认绑定')
                    elif isinstance(text, str):
                        covered.add((source['reference'], source['sha256'], text))
                        location = {key: source.get(key) for key in ('sheet', 'row', 'field')}
                        if any(value is not None for value in location.values()):
                            match = next((check for check in detail_checks if check['source_ref'] == source), None)
                            if match is None or match['text'] != text:
                                error(req_path, '附件明细定位或预期值与当前确认明细不一致')
                            else:
                                detail_covered.add(match['requirement_id'])
                else:
                    error(req_path, '来源种类只允许 answer 或 attachment')
            refs = requirement.get('evidence_refs')
            if not isinstance(refs, list) or (isinstance(req_result, str) and req_result in DEFINITIVE and not refs):
                error(req_path, '明确要求结果必须提供真实证据')
            elif any(not evidence_valid(ref, rid) for ref in refs):
                error(req_path, '证据未匹配真实材料、人工签认或成功 Odoo 追踪')
            if requirement.get('verification_target') == 'configuration' and isinstance(req_result, str) and req_result in DEFINITIVE:
                if not isinstance(refs, list) or not any(isinstance(ref, dict) and ref.get('kind') == 'odoo' and evidence_valid(ref, rid, req_result) for ref in refs):
                    error(req_path, '系统配置结果必须匹配当前只读追踪中的结构化预期比较状态；须重新查询并比较')
            if requirement.get('verification_target') == 'document' and isinstance(req_result, str) and req_result in DEFINITIVE:
                if not isinstance(refs, list) or not any(isinstance(ref, dict) and ref.get('kind') in ('document', 'manual') for ref in refs):
                    error(req_path, '材料核验必须提供独立文件或人工签认证据，系统查询不能替代')
            if (requirement.get('verification_target') == 'business' or record.get('verification_target') == 'business') and isinstance(req_result, str) and req_result in {'通过', '不适用'}:
                if not isinstance(refs, list) or not refs or all(isinstance(ref, dict) and ref.get('kind') == 'odoo' for ref in refs):
                    error(req_path, '只读配置不能证明真实业务操作效果，须提供材料或人工签认')
        for attachment in attachments.values():
            for text in attachment['requirements']:
                if (attachment['reference'], attachment['sha256'], text) not in covered:
                    error(path + '.requirements', '绑定附件中的每项已确认要求均须逐项覆盖')
        if detail_covered != {check['requirement_id'] for check in detail_checks}:
            error(path + '.requirements', '绑定附件明细每个非空字段均须保留核验项，未核验可标待确认或未检查')
        if requirements:
            aggregated = _aggregate(requirement_results)
            if (aggregated == '待确认' and (not isinstance(result, str) or result not in {'待确认', '未检查', '未知', '部分通过'})) or (aggregated != '待确认' and result != aggregated):
                error(path + '.result', '问题结果必须与逐项要求聚合一致')
        refs = record.get('evidence_refs')
        if not isinstance(refs, list) or (isinstance(result, str) and result in DEFINITIVE and not refs):
            error(path + '.evidence_refs', '明确问题结果必须提供真实证据')
        elif any(not evidence_valid(ref) for ref in refs):
            error(path + '.evidence_refs', '问题证据不匹配当前真实依据')
    if seen != set(questions):
        error('records', '模板问题必须逐一保留，未完成项标未检查或待确认')
    coverage = {'|'.join(str(basis.get(k, '')) for k in ('module', 'scope_id', 'environment')):
                {'inspected': len(seen), 'template': len(questions), 'excluded': 0}}
    return {'valid': not errors, 'gate': _aggregate(outcomes) if not errors else '待确认', 'errors': errors, 'basis': basis,
            'template_revision': state['template_revision'] if state else None,
            'validated_at': datetime.now(timezone.utc).isoformat(), 'execution_authorized': False,
            'coverage': coverage, 'limitations': '仅校验当前模板覆盖、版本、结构与真实引用关系；不证明模型已完整理解答案自由文本业务语义。'
            '只读配置不能证明支付、制证等实际业务效果；未验证边界须保留待确认，不代表项目整体交付通过。'}
