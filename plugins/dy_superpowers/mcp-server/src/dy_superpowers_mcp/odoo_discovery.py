"""只读发现与诊断；复用当前环境连接，不依赖 Docker 或直连数据库。"""
import re
from .odoo_jsonrpc import OdooConfigurationError, OdooRPCError
from .qa_store import read_qa
from . import knowledge


def discover(client, kind, arguments):
    offset, limit = arguments.get('offset', 0), arguments.get('limit', 100)
    domain = []
    if kind == 'module_list_available':
        model = 'ir.module.module'
        fields = ['name', 'shortdesc', 'state', 'latest_version', 'category_id']
        if arguments.get('category'):
            domain.append(['category_id.name', 'ilike', arguments['category']])
        key = 'modules'
    elif kind == 'model_list':
        model, fields, key = 'ir.model', ['model', 'name', 'state', 'transient'], 'models'
        if not arguments.get('transient', False):
            domain.append(['transient', '=', False])
        if arguments.get('search'):
            domain += ['|', ['model', 'ilike', arguments['search']], ['name', 'ilike', arguments['search']]]
    else:
        model, fields, key = 'ir.model.fields', ['model', 'name', 'field_description', 'ttype', 'relation'], 'fields'
        domain += ['|', ['name', 'ilike', arguments['query']], ['field_description', 'ilike', arguments['query']]]
        if arguments.get('model'):
            domain.append(['model', '=', arguments['model']])
    # 多取一条判断是否还有下一页，避免恰好满页时误报 has_more。
    rows = client.search_read(model, domain, fields, limit + 1, offset)
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {key: rows, 'count': len(rows), 'offset': offset, 'limit': limit,
            'has_more': has_more, 'next_offset': offset + limit if has_more else None}


def installed_modules(client):
    """满页续查；重复或异常分页不能视为完整覆盖。"""
    rows, seen = [], set()
    for offset in range(0, 10000, 100):
        page = client.module_records(installed_only=True, offset=offset) if hasattr(client, 'module_records') else client.search_read(
            'ir.module.module', [['state', '=', 'installed']], ['name', 'state'], 100, offset)
        for row in page:
            name = row.get('name')
            if not isinstance(name, str) or name in seen:
                raise OdooRPCError('模块分页不完整或存在重复，请重新核对')
            seen.add(name)
            if row.get('state') == 'installed':
                rows.append(row)
        if len(page) < 100:
            return rows
    raise OdooRPCError('模块查询超过安全分页上限，不能判定完整')


def health_check(client_factory):
    checks = []
    client = None
    try:
        client = client_factory()
        checks.append({'check': '认证连接', 'status': 'pass'})
    except (OdooConfigurationError, OdooRPCError):
        checks.append({'check': '认证连接', 'status': 'fail', 'details': '配置缺失、连接或认证失败；不返回远端正文'})
    if client:
        try:
            version = client._rpc('common', 'version', [])
            if not isinstance(version, dict) or not isinstance(version.get('server_version'), str):
                raise OdooRPCError('版本返回格式无效')
            checks.append({'check': '服务版本', 'status': 'pass', 'version': version['server_version']})
        except OdooRPCError:
            checks.append({'check': '服务版本', 'status': 'fail'})
        try:
            rows = installed_modules(client)
            checks.append({'check': '模块读取', 'status': 'pass', 'count': len(rows)})
        except OdooRPCError:
            checks.append({'check': '模块读取', 'status': 'fail'})
    checks += [{'check': item, 'status': 'not_checked', 'details': '当前工具不提供宿主或数据库直连检查'}
               for item in ('Docker', '数据库直连', '磁盘和服务日志')]
    failed = sum(c['status'] == 'fail' for c in checks)
    return {'overall': 'unhealthy' if failed else 'degraded', 'checks': checks,
            'passed': sum(c['status'] == 'pass' for c in checks), 'failures': failed,
            'accepted': False, 'limitations': '连接可用不证明业务配置对齐；宿主检查未执行。'}


def community_gaps(workspace, client, module, scope_id):
    """候选差异只锚定当前确认答案；不把通用互补模块建议当作企业缺陷。"""
    state = read_qa(workspace, module, scope_id)
    installed = {r['name'] for r in installed_modules(client)}
    gaps, candidates = [], []
    for answer in state['records']:
        texts = [answer['answer']]
        for attachment in answer.get('attachments', []):
            texts.extend(attachment.get('requirements', []))
            for detail in attachment.get('details', []):
                texts.extend(str(value) for row in detail['rows'] for value in row.values() if value is not None)
        # 字面提及不是肯定交付要求（可能为“不需要”）；仅输出待语义核实的候选。
        text = '；'.join(texts)
        hits = []
        for phrase in texts:
            for start in range(0, len(phrase), 200):
                hits.extend(knowledge.search(phrase[start:start + 200], knowledge_type='summary')['results'])
        names = {word for word in re.findall(r'\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b', text)}
        names.update(r['module'] for r in hits)
        for name in sorted(names):
            info = knowledge.module_info(name)
            if not info.get('found'):
                continue
            item = {'question_id': answer['question_id'], 'module': name,
                    'status': 'installed' if name in installed else 'not_installed',
                    'confirmed_requirement': False, 'reason': '答案提及此模块；需结合否定、范围、例外确认是否应交付'}
            candidates.append(item)
            if name not in installed:
                gaps.append(item)
    return {'module': module, 'scope_id': state['scope_id'], 'answer_version': state['answer_version'],
            'gaps': gaps, 'candidates': candidates, 'accepted': False, 'execution_authorized': False,
            'attachment_integrity': state['attachment_integrity'],
            'limitations': '候选差异不是配置缺陷；不能覆盖自由文本全部业务含义，须逐题巡检。'}
