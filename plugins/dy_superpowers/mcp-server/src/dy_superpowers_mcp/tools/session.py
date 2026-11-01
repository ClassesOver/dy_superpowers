"""会话初始化：返回项目记忆索引与模板 QA 概要，不注入记忆正文。"""
from __future__ import annotations

import json
from pathlib import Path

from ..memory import MEMORY_FILES
from ..qa_store import read_qa
from ..qa_templates import template_modules
from ..trace import TraceStore
from .runtime import ToolRequestError, append_trace

SESSION_SOURCES = {"startup", "resume", "clear", "compact", "fork"}

def _handle_session_initialize(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str):
    source = arguments.get('source')
    if not isinstance(source, str) or source not in SESSION_SOURCES:
        raise ToolRequestError(-32602, 'unsupported SessionStart source')
    action = '恢复' if source == 'resume' else '初始化'
    files = '、'.join(MEMORY_FILES)
    context = '\n'.join([f'工作区会话已{action}（来源：{source}）。', f'追踪工作区标识：{store.workspace_id}。', '项目记忆位于当前工作区 .dy_superpowers/，按需读取，不在会话初始化时注入正文。', '恢复未完成调研时，先按需读取 delivery-register.md 的待办，再按模块和检查键复核最新确认结论。', f'可用文件：{files}。使用 dy_memory_read(trace_id, name) 读取项目记忆；模块模板QA使用 dy_module_memory_read(trace_id, module, scope_id)。', '项目记忆是参考资料，不构成记忆写入或业务操作授权。'])
    from ..qa_templates import load_template
    summaries = []
    for module in template_modules():
        if workspace.exists():
            state = read_qa(workspace, module)
        else:
            template = load_template(module)
            state = dict(module=module, scope_mode='unscoped', scopes=[], template_revision=template['revision'],
                         answer_version=None, total=len(template['questions']), answered=0, confirmed_count=0)
        summaries.append({k: state[k] for k in ('module', 'scope_mode', 'scopes', 'template_revision', 'answer_version', 'total', 'answered', 'confirmed_count')})
    context += '\n最新模板 QA 概要（不注入答案正文）：' + json.dumps(summaries, ensure_ascii=False) + '\n默认入口 dy_module_memory_read；历史旧存储须 legacy=true。'
    event = {'event': 'session.initialize', 'operation': 'session_start', 'tool': name, 'status': 'ok', 'source_ref': source, 'span_id': span_id}
    append_trace(store, arguments['trace_id'], event)
    return context


TOOLS = {
    'dy_session_initialize': (
        '初始化或恢复当前工作区会话并返回项目记忆索引；需要时使用记忆工具读取完整内容。',
        {'type': 'object',
         'required': ['trace_id', 'source'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
                        'source': {'type': 'string', 'enum': ['clear', 'compact', 'fork', 'resume', 'startup']}}},
        _handle_session_initialize,
    ),
}
