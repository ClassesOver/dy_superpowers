"""本地 HRP 知识检索工具。"""
from __future__ import annotations

from pathlib import Path

from .. import knowledge as knowledge_lib
from ..trace import TraceStore
from .runtime import ToolRequestError, invalid_arguments


def _handle_knowledge_module_info(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str):
    with invalid_arguments():
        return knowledge_lib.module_info(arguments.get('module', ''))


def _handle_knowledge_search(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str):
    query = arguments.get('query')
    if not isinstance(query, str) or not query.strip():
        raise ToolRequestError(-32602, 'query 必须是非空文本')
    with invalid_arguments():
        return knowledge_lib.search(query, **{key: arguments[key] for key in ('module', 'knowledge_type', 'limit', 'retrieval_mode') if key in arguments})


def _handle_knowledge_read(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str):
    with invalid_arguments():
        return knowledge_lib.read(arguments['reference'], offset=arguments.get('offset', 0), limit=arguments.get('limit', 4000))


TOOLS = {
    'dy_knowledge_module_info': (
        '读取本地 HRP/Odoo 模块知识。',
        {'type': 'object',
         'required': ['trace_id', 'module'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'}, 'module': {'type': 'string'}}},
        _handle_knowledge_module_info,
    ),
    'dy_knowledge_search': (
        '检索本地HRP源码及业务摘要；技术名按字面查询，中文短语无源码命中时拆词，先同时匹配全部词，无结果才放宽为任一词。search_strategy/search_terms/query_relaxed说明实际查询。按reference续读，不证明语义正确；不支持模型/字段语义过滤。',
        {'type': 'object', 'additionalProperties': False,
         'required': ['trace_id', 'query'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'}, 'query': {'type': 'string', 'minLength': 1, 'maxLength': 1024},
                        'module': {'type': 'string', 'minLength': 1},
                        'knowledge_type': {'type': 'string', 'enum': ['source', 'summary']},
                        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50},
                        'retrieval_mode': {'type': 'string', 'enum': ['lexical', 'hybrid'], 'default': 'lexical'}}},
        _handle_knowledge_search,
    ),
    'dy_knowledge_read': (
        '按 Knowledge 返回的 reference 分段读取规则或代码；不接受文件路径。',
        {'type': 'object', 'additionalProperties': False,
         'required': ['trace_id', 'reference'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
                        'reference': {'type': 'string', 'minLength': 1, 'maxLength': 2048},
                        'offset': {'type': 'integer', 'minimum': 0},
                        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 16000}}},
        _handle_knowledge_read,
    ),
}
