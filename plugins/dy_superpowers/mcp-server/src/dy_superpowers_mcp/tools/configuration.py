"""受限配置写入工具（create/update）。"""
from __future__ import annotations

from ..configuration import CONFIGURATION_SCHEMAS, write_configuration
from ..odoo_jsonrpc import OdooConfigurationError, OdooRPCError
from ..trace import TraceStore
from .runtime import ToolRequestError, append_trace
from pathlib import Path


def _handle_configuration_write(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str):
    operation = 'create' if name == 'dy_odoo_record_create' else 'update'
    try:
        result = write_configuration(workspace, operation, arguments)
    except OdooConfigurationError as exc:
        raise ToolRequestError(-32602, str(exc)) from exc
    except OdooRPCError as exc:
        raise ToolRequestError(-32603, str(exc)) from exc
    event = {'event': 'odoo.configuration', 'operation': operation, 'tool': name,
             'status': result.get('execution_status', 'not_executed'),
             'model': arguments['model'], 'target_id': result.get('record_id'),
             'field_count': len(arguments['values']), 'span_id': span_id}
    append_trace(store, arguments['trace_id'], event)
    return result


TOOLS = {
}
for operation, schema in CONFIGURATION_SCHEMAS.items():
    TOOLS[f'dy_odoo_record_{operation}'] = (
        '受限 HRP 配置写入；默认关闭。先 dry_run 预览摘要，经用户确认后写入并回读。按答卷配置时 trace_context 须完整携带 module、scope_id、template_revision、question_id、answer_revision、environment；摘要绑定该答卷与服务连接身份，答卷修订、附件变化或连接变化后拒绝。不授予批次验收通过。',
        schema, _handle_configuration_write)
