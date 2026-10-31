"""原生 Zoekt 完整源码检索及独立标记的历史业务摘要；不解析业务依赖。"""
from __future__ import annotations
import base64
from functools import lru_cache
import hashlib
import json
import os
import platform
import re
from pathlib import Path, PurePosixPath
from typing import Any

from .zoekt_service import ZoektService
from .semantic import RemoteEmbedder, SemanticIndex, SemanticUnavailable, chunk_text, rrf_merge

INDEX_DIR = Path(__file__).parent / 'data' / 'zoekt'
BINARY_PATH = Path(__file__).parent / 'data' / 'bin' / 'linux-amd64' / 'zoekt-webserver'
RUNTIME_ROOT = None
SEMANTIC_INDEX_PATH = Path(__file__).parent / 'data' / 'semantic.sqlite3'
SEMANTIC_MODEL_DIR = Path(__file__).parent / 'data' / 'semantic-model'
LIMITATIONS = '源码按文件策略收录，存在排除项；无结果不证明不存在。不自动解析模型、字段或版本，不证明线上安装状态或工作流；自定义字段、定制 addon 与线上版本须通过 dy_odoo_* 核验。'

MODULES: dict[str, dict[str, Any]] = {
    # ---- 费用巡检域（源码仓库 dy-expense / dy-acc-center / dy-budget / dy-hrfm） ----
    "dy_expense": {
        "name": "费用管理",
        "aliases": ["费用", "报销", "差旅", "借款", "还款", "费用单据"],
        "models": [
            "dy.expense.requisition", "dy.expense.payment.requisition", "dy.expense.repayment.requisition",
            "dy.expense.document.type", "dy.expense.document.type.sequence",
            "dy.expense.item", "dy.expense.application.item", "dy.expense.city.season",
            "dy.expense.permission.configuration", "dy.expense.approval.task",
            "dy.expense.credit.score.config", "dy.expense.detail.report",
            "dy.expense.report", "dy.expense.report.payment",
            "dy.expense.policy", "dy.expense.policy.line",
        ],
        "workflow": "申请 → 借款/报销 → 审批 → 付款/核销；单据类型、编号和权限由 dy.expense.document.type 与 dy.expense.permission.configuration 配置",
        "model_notes": {
            "dy.expense.document.type": "公司范围为 company_ids（Many2many），启用字段为 is_active；主表没有独立 code 字段，不要套用 company_id/active/code",
            "dy.expense.document.type.sequence": "编号分配用 company_id/doc_type_id/doc_sequence_id 核对",
            "dy.expense.city.season": "low_season_ids/peak_season_ids/active；本模型无独立公司和有效期字段，季节关联本身不能证明月份与政策覆盖",
            "dy.expense.policy": "公司范围可同时有 company_id 与 company_ids；明细 dy.expense.policy.line 用 city_id/season_type/effective_date/start_date/end_date/expiry_date/control_level 等核对适用政策",
        },
        "depends": ["dy_fin_mdm"],
        "source_repo": "dy-expense",
    },
    "ocr_expense": {
        "name": "OCR&费用管理集成",
        "aliases": ["OCR", "发票识别", "费用OCR"],
        "models": ["dy.ocr.expense.item.rule", "dy.expense.book", "dy.expense.report"],
        "workflow": "源码含 OCR 费用规则、费用台账和报销单扩展；实际识别与审批流程须在目标账套核验",
        "depends": ["ocr_base", "dy_mobile_base", "mobile_app_approval", "dy_expense", "mobile_app_fill", "mobile_expense"],
        "source_repo": "dy-mobile-service",
    },
    "dy_bank_base": {
        "name": "银医基础平台",
        "aliases": ["银医", "银企", "银行接口", "银行付款"],
        "models": ["dy.bank.payment.order", "dy.bank.payment.order.line"],
        "workflow": "源码提供银医付款单模型；银行开通、支付能力和线上流程须在目标账套核验",
        "depends": ["dy_base", "dy_fin_mdm"],
        "source_repo": "dy-bank-payment",
    },
    "dy_bank_expense": {
        "name": "银医平台&费用报销集成",
        "aliases": ["银医", "银企费用", "费用银行付款"],
        "models": ["dy.bank.payment.order", "dy.expense.report", "dy.expense.report.payment"],
        "workflow": "源码扩展银医付款单、费用报销单与付款单；模块依赖不证明线上付款链路已启用",
        "depends": ["dy_bank_base", "dy_expense"],
        "source_repo": "dy-bank-payment",
    },
    "mobile_expense": {
        "name": "移动端费用个性化",
        "aliases": ["移动费用", "移动端费用", "移动报销"],
        "models": ["dy.expense.report.mobile.iface", "dy.expense.payment.requisition.mobile.iface"],
        "workflow": "源码含费用报销与借款申请移动接口；实际移动入口与审批配置须在目标账套核验",
        "depends": ["dy_mobile_base", "mobile_app_fill", "mobile_app_approval", "dy_expense"],
        "source_repo": "dy-mobile-service",
    },
    "dy_purchase": {
        "name": "鼎医采购管理",
        "aliases": ["鼎医采购", "HRP采购", "采购管理", "采购申请"],
        "models": ["dy.purchase.requisition.order", "dy.purchase.requisition.order.line", "purchase.order"],
        "workflow": "源码定义采购申请并扩展采购订单；实际采购流程须在目标账套核验",
        "depends": ["dy_scm_mdm"],
        "source_repo": "dy-scm",
    },
    "dy_fin_expense_pf": {
        "name": "费用财务会计平台（会计平台-费用）",
        "aliases": ["费用制证", "会计平台费用", "费用凭证"],
        "models": ["dy.fin.expense.pf"],
        "workflow": "按 dy.fin.pf.account.move.generation.rule 生成规则把费用单据生成财务凭证",
        "depends": ["dy_expense", "dy_fin_pf"],
        "source_repo": "dy-acc-center",
    },
    "dy_fin_budget_expense_pf": {
        "name": "费用管理预算会计平台（仅公立医院）",
        "aliases": ["费用预算会计", "预算会计平台费用"],
        "models": [],
        "workflow": "在费用财务会计平台之上叠加预算会计记账（平行记账）；自身无新模型，扩展费用单据与预算凭证衔接",
        "depends": ["dy_fin_expense_pf", "dy_fin_budget_pf"],
        "source_repo": "dy-acc-center",
    },
    "dy_budget_control_expense": {
        "name": "预算控制&费用报销集成",
        "aliases": ["费用预算控制", "预算占用", "预算扣减"],
        "models": ["dy.budget.control.execution.rule", "dy.budget.control.rule.expense.item.line", "dy.budget.control.exp.app.line"],
        "workflow": "费用预算控制主规则在 dy_budget_control；费用集成提供费用项目控制行。须按 source_doc、company_id、date_from/date_to、is_active_flag、effective_node 及 expense_item_ids 组合核对",
        "depends": ["dy_budget_control", "dy_expense"],
        "source_repo": "dy-budget",
    },
    "dy_hrfm": {
        "name": "科研管理（专项经费）",
        "aliases": ["科研", "专项经费", "经费", "课题"],
        "models": [
            "dy.hrfm.funds.application", "dy.hrfm.budget.project", "dy.hrfm.funds.account",
            "dy.hrfm.expense.execute.rule", "dy.hrfm.budget.adjust",
        ],
        "workflow": "科研项目立项 → 经费预算 → 经费申请/认领 → 费用执行 → 结题",
        "depends": ["dy_base", "dy_base_data", "dy_fin_mdm"],
        "source_repo": "dy-hrfm",
    },
    "dy_hrfm_expense": {
        "name": "科研管理&费用管理集成",
        "aliases": ["专项经费管理&费用管理集成", "科研费用集成", "经费费用衔接"],
        "models": ["dy.hrfm.expense.execute.rule"],
        "workflow": "集成模块扩展科研与费用衔接；执行规则模型由基础模块 dy_hrfm 定义，基础模块已安装不代表集成已安装",
        "depends": ["dy_hrfm", "dy_expense"],
        "source_repo": "dy-hrfm",
    },
    # ---- 总账巡检域（源码仓库 dy-finance / dy-acc-center） ----
    "dy_fin_mdm": {
        "name": "总账主数据（财务会计-基础）",
        "aliases": ["主数据", "科目", "会计期间", "辅助核算", "现金流映射", "科目权限"],
        "models": [
            "dy.fin.account.structure", "dy.fin.account.type", "dy.fin.account.period",
            "dy.fin.account.period.open.and.done", "dy.fin.account.period.template",
            "dy.fin.account.analytic.plan", "dy.fin.cash.flow.mapping",
            "dy.account.permissions.settings", "dy.account.permissions.account",
        ],
        "workflow": "科目结构/类型 → 会计期间模板与公司分配 → 期间开关 → 辅助核算与现金流映射",
        "model_notes": {
            "dy.fin.account.period": "持久模型；以 company_id/date_from/date_to/account_structure_id 限定范围，state/state_show/cashier_period_state 为状态线索",
            "dy.fin.account.period.open.and.done": "TransientModel 操作向导，不是期间状态事实源；只读巡检不得创建向导或调用开关账方法",
        },
        "depends": ["dy_base", "account_accountant", "dy_base_data"],
        "source_repo": "dy-finance",
    },
    "dy_fin_acc": {
        "name": "总账-凭证管理",
        "aliases": ["凭证", "总账凭证", "过账", "结转", "账簿"],
        "models": [
            "account.move", "account.move.line", "dy.fin.acc.account.periodicity.voucher",
            "account.move.generate", "dy.fin.acc.account.move.summary",
            "dy.fin.acc.account.move.discrete.number",
        ],
        "workflow": "凭证：草稿 → 审核签字 → 过账 → 反过账/作废；期末结转通过结转项目、模板和执行记录生成财务或预算凭证，周期凭证模板用于经常性凭证",
        "depends": ["dy_fin_mdm"],
        "source_repo": "dy-finance",
    },
    "dy_fin_gaap": {
        "name": "总账-预算会计",
        "aliases": ["预算会计", "平行记账", "预算凭证", "预算科目映射", "收支表"],
        "models": [
            "account.mapping", "budget.account.move", "budget.account.move.line",
            "budget.account.balance.sheet", "budget.statement.of.income.and.expenditure",
            "budget.move.paralleled.wizard",
        ],
        "workflow": "财务/预算科目映射（account.mapping）→ 预算凭证平行记账 → 预算报表与结转",
        "depends": ["dy_fin_acc"],
        "source_repo": "dy-finance",
    },
    "dy_fin_pf": {
        "name": "财务会计平台-基础",
        "aliases": ["会计平台", "凭证生成规则", "凭证分类"],
        "models": [
            "dy.fin.pf.account.class.define", "dy.fin.pf.account.move.generation.rule",
            "dy.fin.pf.condition.source.mapping", "dy.fin.pf.influence.factor.setting",
            "dy.fin.voucher.generate.program",
        ],
        "workflow": "凭证分类定义 → 生成规则与条件映射 → 影响因素 → 按业务单据生成凭证",
        "depends": ["dy_fin_acc"],
        "source_repo": "dy-acc-center",
    },
    "dy_fin_budget_pf": {
        "name": "预算会计平台-基础（仅公立医院）",
        "aliases": ["预算会计平台", "预算平台基础"],
        "models": [],
        "workflow": "在财务会计平台与预算会计之上提供预算凭证生成通道；自身无新模型",
        "depends": ["dy_fin_pf", "dy_fin_gaap"],
        "source_repo": "dy-acc-center",
    },
    # ---- Odoo 12 标准基座（概念摘要，非源码仓库内容） ----
    "account": {"name": "财务会计（Odoo 12 标准模块）", "aliases": ["财务", "会计", "总账", "发票"], "models": ["account.move", "account.move.line", "account.journal", "account.account"], "workflow": "会计分录：草稿 → 已过账；付款与核销需单独验证", "depends": ["base"]},
    "hr": {"name": "人力资源", "aliases": ["人事", "员工", "HR"], "models": ["hr.employee", "hr.department", "hr.job"], "workflow": "员工主数据维护", "depends": ["base"]},
    "purchase": {"name": "采购", "aliases": ["采购", "供应商", "采购订单"], "models": ["purchase.order", "purchase.order.line"], "workflow": "询价单 → 采购订单 → 收货 → 开票", "depends": ["account"]},
}


@lru_cache(maxsize=4)
def _load_service(index: Path, binary: Path, runtime_root: Path | None) -> ZoektService:
    if platform.system() != 'Linux' or platform.machine() not in {'x86_64', 'amd64'}:
        raise ValueError('当前 Zoekt 预编译程序仅支持 Linux x86_64，不在安装时编译')
    try:
        manifest = json.loads((index / 'bundle.json').read_text(encoding='utf-8'))
        with binary.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if manifest['status'] != 'verified' or manifest['format'] != 1 or digest != manifest['server_sha256']:
            raise ValueError('Zoekt 索引或程序校验不符')
        if not (index / 'SHA256SUMS').is_file():
            raise ValueError('Zoekt 索引缺少校验清单')
        return ZoektService(index, binary, runtime_root=runtime_root)
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('Zoekt 程序或源码索引未正确打包，不能检索源码') from exc


def _source_service() -> ZoektService:
    return _load_service(INDEX_DIR, BINARY_PATH, RUNTIME_ROOT)


@lru_cache(maxsize=1)
def _semantic_service() -> SemanticIndex:
    """模型必须来自随插件安装的本地产物；运行时不下载。"""
    provider = os.environ.get('DY_KNOWLEDGE_EMBEDDING_PROVIDER', 'local').strip().lower()
    if provider == 'remote':
        endpoint = os.environ.get('DY_KNOWLEDGE_EMBEDDING_URL', '').strip()
        token = os.environ.get('DY_KNOWLEDGE_EMBEDDING_TOKEN', '')
        model = os.environ.get('DY_KNOWLEDGE_EMBEDDING_MODEL', '').strip()
        if not endpoint or not token or not model:
            raise SemanticUnavailable('远程 embedding 配置不完整')
        try:
            return SemanticIndex(SEMANTIC_INDEX_PATH,
                                 embedder=RemoteEmbedder(endpoint, token, model, timeout=10.0))
        except SemanticUnavailable:
            raise
        except Exception as exc:
            raise SemanticUnavailable('远程 embedding 初始化失败') from exc
    if provider != 'local':
        raise SemanticUnavailable('embedding provider 仅支持 local 或 remote')
    if not SEMANTIC_MODEL_DIR.is_dir():
        raise SemanticUnavailable('本地语义模型未安装')
    try:
        from fastembed import TextEmbedding
        # FastEmbed 不同版本对 local_files_only 的签名不同；离线环境变量保证不会静默下载。
        os.environ.setdefault('HF_HUB_OFFLINE', '1')
        os.environ.setdefault('FASTEMBED_CACHE_PATH', str(SEMANTIC_MODEL_DIR))
        embedder = TextEmbedding(model_name='BAAI/bge-small-zh-v1.5', cache_dir=str(SEMANTIC_MODEL_DIR))
    except Exception as exc:
        raise SemanticUnavailable('本地语义模型加载失败') from exc
    return SemanticIndex(SEMANTIC_INDEX_PATH, embedder=embedder)


def _quote(pattern: str) -> str:
    return json.dumps(pattern, ensure_ascii=False)


def _literal(text: str) -> str:
    # Go regexp.QuoteMeta：不使用会额外转义空格的 Python re.escape。
    return re.sub(r'([\\.+*?()|\[\]{}^$])', r'\\\1', text)


def _query_result(service: ZoektService, expression: str, limit: int, *, whole=False) -> dict:
    reply = service.request('/api/search', {'Q': 'case:no repo:"^hrp$" ' + expression,
        'Opts': {'MaxDocDisplayCount': limit, 'MaxMatchDisplayCount': 1000,
                 'ShardMaxMatchCount': 20000, 'TotalMaxMatchCount': 20000,
                 'MaxWallTime': 2000000000, 'Whole': whole}})
    result = reply.get('Result')
    if not isinstance(result, dict) or result.get('Crashes'):
        raise ValueError('源码检索未成功，不能据此认定无结果')
    return result


def _coverage(service: ZoektService) -> dict:
    return {'backend': 'zoekt', 'status': 'policy_filtered', 'bundle_id': service.bundle_id,
            'version_match': 'not_checked', 'limitations': LIMITATIONS}


def _public_source(service: ZoektService, row: dict) -> dict:
    path = row['FileName']
    payload = base64.urlsafe_b64encode(json.dumps(['hrp', path], ensure_ascii=False,
                                                separators=(',', ':')).encode()).decode().rstrip('=')
    lines = row.get('LineMatches') or []
    line = lines[0] if lines else {}
    snippet = base64.b64decode(line.get('Line') or '').decode('utf-8')
    parts = PurePosixPath(path).parts
    return {'module': parts[1] if len(parts) > 2 else None, 'name': path, 'path': path,
            'models': [], 'knowledge_type': 'source', 'fact_type': 'source_text', 'namespace': 'hrp',
            'symbol': PurePosixPath(path).name, 'line': line.get('LineNumber'),
            'summary': (snippet.strip() or path)[:240],
            'reference': f'zoekt:{service.bundle_id}:{payload}'}


def _public_semantic(service: ZoektService, row: dict) -> dict:
    path = row['path']
    payload = base64.urlsafe_b64encode(json.dumps(['hrp', path], ensure_ascii=False,
                                                separators=(',', ':')).encode()).decode().rstrip('=')
    parts = PurePosixPath(path).parts
    return {'module': row.get('module') or (parts[1] if len(parts) > 2 else None),
            'name': path, 'path': path, 'models': [], 'knowledge_type': 'source',
            'fact_type': 'source_text', 'namespace': 'hrp', 'symbol': PurePosixPath(path).name,
            'line': row.get('line'), 'summary': row.get('summary') or path,
            'semantic_score': row.get('semantic_score'),
            'reference': f'zoekt:{service.bundle_id}:{payload}'}


def _rank_sources(service, rows, terms):
    """仅重排有限候选；综合所有命中行，保留 Zoekt 同分顺序。"""
    terms = [term.lower() for term in terms]

    def text(line):
        return base64.b64decode(line.get('Line') or '').decode('utf-8')

    def score(row):
        path = row['FileName'].lower()
        content = path + ' ' + ' '.join(text(line) for line in row.get('LineMatches') or []).lower()
        return ('/i18n/' not in path and not path.endswith(('.po', '.pot')),
                sum(term in content for term in terms),
                path.endswith(('.py', '.xml', '.js')))

    sources = []
    for row in sorted(rows, key=score, reverse=True):
        lines = row.get('LineMatches') or []
        if lines:
            best = max(lines, key=lambda line: sum(term in text(line).lower() for term in terms))
            row = row | {'LineMatches': [best]}
        sources.append(_public_source(service, row))
    return sources


def module_info(module: str) -> dict[str, Any]:
    if not isinstance(module, str) or not re.fullmatch(r'[A-Za-z0-9_]+', module):
        raise ValueError('module 必须是模块技术名')
    service = _source_service()
    result = _query_result(service, 'file:' + _quote('(^|/)' + _literal(module) + '/__manifest__\\.py$'), 10)
    if result.get('FilesSkipped') or result.get('ShardsSkipped'):
        raise ValueError('模块源码检索不完整，不能确认模块是否存在，请重试')
    references = [_public_source(service, row) for row in result.get('Files') or []]
    info = MODULES.get(module)
    response = {'found': bool(info or references), 'module': module, 'references': references,
                'coverage': _coverage(service), 'version_match': 'not_checked',
                'odoo_version': 'unknown', 'limitations': LIMITATIONS,
                'semantic_indexing': False, 'indexed_models': None, 'declared_dependencies': None,
                'indexed_versions': None, 'indexed_odoo_versions': None}
    if info:
        response.update(info)
        response.update(fact_type='curated_interpretation', summary_odoo_version='12.0',
                        interpretation_fields=list(info))
        if 'source_repo' not in info:
            response['source'] = 'Odoo 12 标准模块概念摘要'
            response.update(odoo_version='12.0', odoo_version_scope='summary')
    elif references:
        response.update(name=module, models=[], depends=[], fact_type='source_text')
    else:
        response['available'] = sorted(MODULES)
    return response


def _question_terms(query):
    if (not re.search(r'[\u4e00-\u9fff]', query)
            or (not re.search(r'问[：:]|如何|哪些|是否|什么|怎么|怎样|[？?]', query)
                and not re.fullmatch(r'[\u4e00-\u9fffA-Za-z0-9\s，。！、；：（）“”‘’,!;()\-]+', query))):
        return []
    import jieba
    jieba.setLogLevel(40)
    stop = {'如何', '哪些', '是否', '什么', '怎么', '进行', '设置', '公司', '各', '的', '和', '与',
            '需要', '目前', '现在', '是否有', '使用', '管理', '相关', '支持', '可以', '通过', '以及'}
    return list(dict.fromkeys(word.strip() for word in jieba.cut(query, HMM=True)
                             if len(word.strip()) >= 2 and word not in stop
                             and re.search(r'[\w\u4e00-\u9fff]', word)))[:12]


def search(query: str, module: str | None = None, model: str | None = None,
           knowledge_type: str | None = None, limit: int = 20,
           retrieval_mode: str = 'lexical') -> dict[str, Any]:
    if not isinstance(query, str) or not query.strip() or len(query) > 1024:
        raise ValueError('query 必须为 1 到 1024 字符的非空文本')
    if module is not None and (not isinstance(module, str) or not re.fullmatch(r'[A-Za-z0-9_]+', module)):
        raise ValueError('module 必须是模块技术名')
    if model is not None or knowledge_type not in (None, 'source', 'summary'):
        raise ValueError('不再支持模型/字段等语义过滤；请将技术名作为 query，knowledge_type 仅支持 source 或 summary')
    if retrieval_mode not in ('lexical', 'hybrid'):
        raise ValueError('retrieval_mode 仅支持 lexical 或 hybrid')
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('limit 必须是 1 到 50 的整数')
    query = query.strip()
    results = []
    terms, strategy = [query], 'literal'
    relaxed = False
    source_count = 0
    if knowledge_type != 'source':
        for key, info in MODULES.items():
            haystack = ' '.join([key, info['name'], *info['aliases'], *info['models']]).lower()
            if query.lower() in haystack and (module is None or module == key):
                results.append({'module': key, 'name': info['name'], 'models': info['models'],
                                'knowledge_type': 'summary', 'fact_type': 'curated_interpretation',
                                'reference': f'summary:{key}', 'summary': info['workflow'][:240],
                                'symbol': key, 'namespace': 'hrp' if 'source_repo' in info else 'standard_concept'})
    total, known = len(results), True
    coverage = {'backend': 'curated_summary', 'status': 'summary_only', 'limitations': LIMITATIONS}
    if knowledge_type != 'summary':
        service = _source_service()
        expression = _quote(_literal(query))
        if module is not None:
            expression += ' file:' + _quote('(^|/)' + _literal(module) + '/')
        raw = _query_result(service, expression, min(200, limit * 6 + 1))
        coverage = _coverage(service)
        total += raw.get('FileCount', 0)
        known = not (raw.get('FilesSkipped') or raw.get('ShardsSkipped'))
        source_count = raw.get('FileCount', 0)
        results.extend(_rank_sources(service, raw.get('Files') or [], terms))
    fallback = _question_terms(query) if known and (
        not results or (knowledge_type != 'summary' and not source_count)) else []
    if fallback:
        terms, strategy = fallback, 'keywords'
        literal_summary_count = len(results)
        if knowledge_type != 'source':
            for key, info in MODULES.items():
                haystack = ' '.join([key, info['name'], *info['aliases'], *info['models']]).lower()
                if (module is None or module == key) and any(term.lower() in haystack for term in terms) and not any(
                        row['reference'] == f'summary:{key}' for row in results):
                    results.append({'module': key, 'name': info['name'], 'models': info['models'],
                                    'knowledge_type': 'summary', 'fact_type': 'curated_interpretation',
                                    'reference': f'summary:{key}', 'summary': info['workflow'][:240],
                                    'symbol': key, 'namespace': 'hrp' if 'source_repo' in info else 'standard_concept'})
        total = len(results)
        if knowledge_type != 'summary':
            expression = '(' + ' '.join(_quote(_literal(term)) for term in terms) + ')'
            if module is not None:
                expression += ' file:' + _quote('(^|/)' + _literal(module) + '/')
            raw = _query_result(service, expression, min(200, limit * 6 + 1))
            if len(terms) > 1 and not raw.get('FileCount') and not (
                    raw.get('FilesSkipped') or raw.get('ShardsSkipped')):
                relaxed = True
                expression = '(' + ' or '.join(_quote(_literal(term)) for term in terms) + ')'
                if module is not None:
                    expression += ' file:' + _quote('(^|/)' + _literal(module) + '/')
                raw = _query_result(service, expression, min(200, limit * 6 + 1))
            total += raw.get('FileCount', 0)
            known = not (raw.get('FilesSkipped') or raw.get('ShardsSkipped'))
            # 关键词泛匹配的摘要置于源码之后；原始短语直接命中的摘要保留。
            results = (results[:literal_summary_count]
                       + _rank_sources(service, raw.get('Files') or [], terms)
                       + results[literal_summary_count:])
    semantic_status = {'status': 'skipped', 'reason': '仅摘要或字面模式'}
    if retrieval_mode == 'hybrid' and knowledge_type != 'summary':
        semantic_status = {'status': 'degraded', 'reason': '未执行'}
        try:
            semantic_index = _semantic_service()
            windows = [piece.text for piece in chunk_text(query, max_chars=1792, overlap_chars=256)] or [query]
            semantic_rows = []
            for window in windows:
                semantic_rows.extend(semantic_index.search(window, module=module, limit=100))
            # 多窗口只保留每个文件最高分，避免同一文件占满候选。
            best_by_path = {}
            for row in semantic_rows:
                if row['path'] not in best_by_path or row.get('semantic_score', -1) > best_by_path[row['path']].get('semantic_score', -1):
                    best_by_path[row['path']] = row
            semantic_rows = sorted(best_by_path.values(), key=lambda row: (-row.get('semantic_score', -1), row['path']))[:100]
            semantic_rows = [_public_semantic(service, row) for row in semantic_rows]
            lexical_rows = [row for row in results if row.get('knowledge_type') == 'source']
            summaries = [row for row in results if row.get('knowledge_type') == 'summary']
            merged = rrf_merge(lexical_rows, semantic_rows, limit)
            results = summaries + merged
            semantic_status = {'status': 'ok', 'reason': None, 'vector_count': len(semantic_rows)}
        except SemanticUnavailable as exc:
            semantic_status = {'status': 'degraded', 'reason': str(exc)}
        except Exception as exc:
            semantic_status = {'status': 'degraded', 'reason': f'语义检索失败：{type(exc).__name__}'}
        total = None
        known = False
    results = results[:limit]
    truncated = not known or total > len(results) if total is not None else True
    response = {'query': query, 'results': results, 'count': len(results), 'total_known': known,
                'total_count': total if known else None, 'truncated': truncated,
                'coverage': coverage, 'limitations': LIMITATIONS,
                'search_strategy': strategy, 'search_terms': terms, 'query_relaxed': relaxed,
                'retrieval_mode': retrieval_mode, 'semantic': semantic_status}
    while len(json.dumps(response, ensure_ascii=False).encode()) > 65536 and results:
        results.pop()
        response.update(count=len(results), truncated=True)
    return response


def read(reference: str, offset: int = 0, limit: int = 4000) -> dict:
    """只通过索引读取完整源码；引用中的名字从不作为本地文件路径打开。"""
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 16000:
        raise ValueError('offset 必须非负，limit 必须为 1 到 16000 的整数')
    if not isinstance(reference, str) or len(reference) > 2048:
        raise ValueError('reference 必须是知识引用')
    stale = {'found': False, 'reference': reference, 'limitations': '引用不存在或索引版本已变化，请重新检索。'}
    if reference.startswith('summary:') and reference[8:] in MODULES:
        content = json.dumps(MODULES[reference[8:]], ensure_ascii=False, indent=2)
        metadata = {'fact_type': 'curated_interpretation', 'namespace': 'summary'}
    elif re.fullmatch(r'src:[0-9a-f]{32}', reference):
        return stale
    elif re.fullmatch(r'zoekt:[0-9a-f]{24}:[A-Za-z0-9_-]+', reference):
        _, version, payload = reference.split(':')
        try:
            repo, path = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
            if repo != 'hrp' or not isinstance(path, str) or not path or path.startswith('/') or any(
                    part in {'', '.', '..'} for part in path.split('/')) or '\x00' in path:
                raise ValueError('无效源码引用')
        except (ValueError, TypeError, UnicodeError) as exc:
            raise ValueError('无效源码引用') from exc
        service = _source_service()
        if version != service.bundle_id:
            return stale
        result = _query_result(service, 'file:' + _quote('^' + _literal(path) + '$'), 2, whole=True)
        if result.get('FilesSkipped') or result.get('ShardsSkipped'):
            raise ValueError('源码读取不完整，请重试；不能认定引用不存在')
        files = result.get('Files') or []
        if len(files) != 1 or files[0]['FileName'] != path:
            return stale
        content = base64.b64decode(files[0].get('Content') or '').decode('utf-8')
        metadata = _public_source(service, files[0]) | {'coverage': _coverage(service), 'source_dependency': False}
    else:
        raise ValueError('reference 仅接受检索返回的 zoekt: 或 summary: 引用')
    end = min(offset + limit, len(content))
    return metadata | {'found': True, 'reference': reference, 'offset': offset, 'content': content[offset:end],
                       'total_length': len(content), 'complete': end == len(content),
                       'next_offset': end if end < len(content) else None, 'limitations': LIMITATIONS}
