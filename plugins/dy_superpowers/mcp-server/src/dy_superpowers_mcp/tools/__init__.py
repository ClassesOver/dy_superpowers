"""MCP 工具注册表：汇总各领域工具，统一 schema、写开关、参数校验与分发。"""
from __future__ import annotations

from contextlib import nullcontext
import os

from . import configuration, inspection, knowledge, memory, odoo, research, session
from ..odoo_capabilities import SCHEMAS as ODOO_CAPABILITY_SCHEMAS
from ..qa_basis import configured_environment_info
from ..trace import TRACE_CODE_PATTERN, TRACE_CONTEXT_FIELDS
from .runtime import ToolRequestError, odoo_lock

# 工具名、说明、schema 和处理器在各领域模块中唯一注册。
TOOL_REGISTRY = {}
for _domain in (odoo, research, session, inspection, memory, knowledge, configuration):
    duplicated = TOOL_REGISTRY.keys() & _domain.TOOLS.keys()
    if duplicated:
        raise RuntimeError(f'工具重复注册：{sorted(duplicated)}')
    TOOL_REGISTRY.update(_domain.TOOLS)

for _, _schema, _ in TOOL_REGISTRY.values():
    _schema['properties']['trace_context'] = {
        'type': 'object', 'description': '可选的巡检关联标识；不包含正文，不代表业务授权或事实验证。',
        'additionalProperties': False,
        'properties': {key: {'type': 'string', 'pattern': TRACE_CODE_PATTERN.pattern}
                       for key in sorted(TRACE_CONTEXT_FIELDS)},
    }
TOOLS = {name: spec[0] for name, spec in TOOL_REGISTRY.items()}
TOOL_SCHEMAS = {name: spec[1] for name, spec in TOOL_REGISTRY.items()}

# 写类工具仅在宿主进程显式开启对应开关时出现在工具列表并可调用；开关不从工作区 .env 加载。
WRITE_SWITCHES = {
    'dy_odoo_record_create': 'DY_ODOO_CONFIG_WRITE_ENABLED',
    'dy_odoo_record_update': 'DY_ODOO_CONFIG_WRITE_ENABLED',
    'dy_odoo_import_execute': 'DY_ODOO_CONFIG_WRITE_ENABLED',
    'dy_odoo_module_install': 'DY_ODOO_MODULE_WRITE_ENABLED',
    'dy_odoo_module_uninstall': 'DY_ODOO_MODULE_WRITE_ENABLED',
}
# 需要核对服务实际连接身份的 Odoo 读取与能力工具。
ODOO_IDENTITY_TOOLS = {'dy_odoo_record_search', 'dy_odoo_record_read', 'dy_odoo_model_fields',
                       'dy_odoo_module_info', 'dy_odoo_module_list_installed', *ODOO_CAPABILITY_SCHEMAS}


def tool_enabled(name: str) -> bool:
    switch = WRITE_SWITCHES.get(name)
    return switch is None or os.environ.get(switch) == '1'


def enabled_tools():
    return [(name, spec) for name, spec in TOOL_REGISTRY.items() if tool_enabled(name)]


_VALIDATORS = {}


def _validator(name):
    """每个工具的 schema 校验器只编译一次。"""
    validator = _VALIDATORS.get(name)
    if validator is None:
        from jsonschema import Draft202012Validator
        validator = _VALIDATORS[name] = Draft202012Validator(TOOL_SCHEMAS[name])
    return validator


def available_validator(name):
    """当前实际可调用工具的已编译校验器；未注册或写开关关闭的工具返回 None。"""
    if not isinstance(name, str) or name not in TOOL_REGISTRY or not tool_enabled(name):
        return None
    return _validator(name)


def dispatch_tool(name, arguments, workspace, store, span_id):
    spec = TOOL_REGISTRY.get(name) if isinstance(name, str) else None
    if spec is None:
        raise ToolRequestError(-32602, "未知工具")
    if not tool_enabled(name):
        raise ToolRequestError(-32602, f"写入工具未启用：需在 MCP 进程环境设置 {WRITE_SWITCHES[name]}=1")
    if next(_validator(name).iter_errors(arguments), None) is not None:
        raise ToolRequestError(-32602, "工具参数不符合 schema，请核对必填字段及类型")
    if name in ODOO_IDENTITY_TOOLS:
        identity = configured_environment_info()
        actual_environment = identity['environment']
        supplied = arguments.get('trace_context', {}).get('environment')
        if supplied is not None and supplied != actual_environment:
            raise ToolRequestError(-32602, 'trace_context.environment 与当前服务实际配置身份不一致')
        fingerprint = identity['connection_fingerprint']
        if arguments.get('trace_context', {}).get('connection_fingerprint', fingerprint) != fingerprint:
            raise ToolRequestError(-32602, '连接身份已变化，请重新准备巡检')
        if actual_environment is not None:
            arguments = {**arguments, 'trace_context': {**arguments.get('trace_context', {}),
                                                       'environment': actual_environment,
                                                       **({'connection_fingerprint': fingerprint} if fingerprint else {})}}
    with odoo_lock if name.startswith("dy_odoo_") or name == 'dy_knowledge_community_gaps' else nullcontext():
        return spec[2](name, arguments, workspace, store, span_id)
