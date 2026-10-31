"""新版 QA 回归共用夹具：只写入测试临时目录。"""
import hashlib
import uuid
from datetime import datetime, timezone

from dy_superpowers_mcp.qa_store import commit_qa, read_qa
from dy_superpowers_mcp.qa_templates import load_template


def save_answers(root, module='expense', count=1, *, scope='甲院/一期', complete=True, answer='本期按确认清单配置'):
    root.mkdir(parents=True, exist_ok=True)
    template = load_template(module)
    state = read_qa(root, module)
    existing = next((s for s in state['scopes'] if s['scope'] == scope), None)
    state = read_qa(root, module, existing['scope_id']) if existing else state
    prior = {r['question_id']: r for r in state['records']} if existing else {}
    material = root / 'evidence.txt'
    material.write_text('经确认的脱敏核验材料', encoding='utf-8')
    bound = [dict(reference='evidence.txt', sha256=hashlib.sha256(material.read_bytes()).hexdigest(), requirements=['应用安装符合清单'])]
    return commit_qa(root, module, scope=scope, scope_id=existing['scope_id'] if existing else None,
        template_revision=template['revision'], batch_id='regression-' + uuid.uuid4().hex.translate(
            str.maketrans('0123456789', 'ghijklmnop')), confirmed=True, confirmed_by='测试',
        answers=[dict(question_id=q['question_id'], answer=answer, research_complete=complete,
            expected_revision=prior.get(q['question_id'], {}).get('revision'), attachments=bound) for q in template['questions'][:count]])


def make_report(root, module='expense', *, state=None, environment='UAT'):
    state = state or save_answers(root, module)
    basis = {k: state[k] for k in ('module', 'scope_id', 'template_revision', 'answer_version')}
    basis['environment'] = environment
    from dy_superpowers_mcp.inspection_prepare import configured_environment_info
    basis['connection_fingerprint'] = configured_environment_info()['connection_fingerprint']
    material = root / 'independent-verification.txt'
    material.write_text('独立核验系统现状的材料', encoding='utf-8')
    answers = {r['question_id']: r for r in state['records']}
    records = []
    for q in state['questions']:
        a = answers.get(q['question_id'])
        refs = [dict(kind='document', reference=material.name, sha256=hashlib.sha256(material.read_bytes()).hexdigest(),
                     observed_at=datetime.now(timezone.utc).isoformat())] if a else []
        requirements = [dict(requirement_id=f'{q["question_id"]}-{n}', text=text,
            source_refs=[dict(kind='answer', question_id=q['question_id'], revision=a['revision']),
                         dict(kind='attachment', reference=b['reference'], sha256=b['sha256'])],
            verification_target='document', result='通过' if a['research_complete'] else '待确认', evidence_refs=refs if a['research_complete'] else [])
            for b in a['attachments'] for n, text in enumerate(b['requirements'])] if a else []
        records.append(dict(question_id=q['question_id'], answer_revision=a['revision'] if a else None,
            result='通过' if a and a['research_complete'] else '待确认' if a else '未检查',
            reason='按本期材料核对' if a else '尚无确认答案', verification_method='材料核对',
            verified_at=datetime.now(timezone.utc).isoformat(), requirements=requirements, evidence_refs=refs if a and a['research_complete'] else []))
    return dict(basis=basis, expected_checks=[dict(question_id=q['question_id']) for q in state['questions']], records=records)
