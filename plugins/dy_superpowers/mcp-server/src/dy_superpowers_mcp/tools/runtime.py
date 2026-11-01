"""工具运行时：参数错误、本地 trace 写入与上下文、Odoo 连接缓存及查询审计。"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import sys
from threading import RLock
from typing import Iterator

from ..odoo_jsonrpc import OdooConfigurationError, OdooReadClient, OdooRPCError, OdooSettings
from ..trace import TRACE_CONTEXT_FIELDS, TraceStore


class ToolRequestError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


# 当前工具调用的 trace 关联字段，自动并入本次调用写出的每条事件。
_trace_context: ContextVar[dict] = ContextVar('dy_trace_context', default={})


@contextmanager
def trace_scope(call_id: str, trace_context: dict | None = None) -> Iterator[None]:
    context = {key: value for key, value in (trace_context or {}).items() if key in TRACE_CONTEXT_FIELDS}
    token = _trace_context.set({**context, 'call_id': call_id})
    try:
        yield
    finally:
        _trace_context.reset(token)


def append_trace(store: TraceStore, trace_id: str, event: dict) -> bool:
    try:
        store.append(trace_id, {**_trace_context.get(), **event})
        return True
    except OSError as exc:
        sys.stderr.write(f"本地操作追踪写入失败（{type(exc).__name__}），请检查日志目录。\n")
        return False


@contextmanager
def invalid_arguments(*errors):
    """把领域校验异常一对一转换为参数错误（-32602）；作用范围与原 try 块一致。"""
    try:
        yield
    except (errors or (ValueError,)) as exc:
        raise ToolRequestError(-32602, str(exc)) from exc


@contextmanager
def odoo_errors(rpc_message=None):
    """Odoo 配置错误转为参数错误；远程调用失败时丢弃缓存连接并转为 -32603。"""
    try:
        yield
    except OdooConfigurationError as exc:
        raise ToolRequestError(-32602, str(exc)) from exc
    except OdooRPCError as exc:
        clear_odoo_client()
        raise ToolRequestError(-32603, rpc_message or str(exc)) from exc


odoo_lock = RLock()
_odoo_cached = None


def clear_odoo_client():
    global _odoo_cached
    with odoo_lock:
        if _odoo_cached is not None:
            try:
                _odoo_cached[2].close()
            except Exception:
                pass
            _odoo_cached = None


def odoo_client(workspace):
    global _odoo_cached
    settings = OdooSettings.from_environment()
    key = (Path(workspace).resolve(), settings)
    with odoo_lock:
        if _odoo_cached is None or _odoo_cached[:2] != key:
            clear_odoo_client()
            _odoo_cached = (*key, OdooReadClient(settings))
        return _odoo_cached[2]


class AuditedOdoo:
    """唯一的 Odoo 查询审计：先记 odoo.query，成功后记 odoo.read；返回行不进入 trace。"""

    def __init__(self, client, store, arguments, tool, span_id):
        self.client, self.store, self.arguments = client, store, arguments
        self.tool, self.span_id = tool, span_id

    def __getattr__(self, key):
        return getattr(self.client, key)

    def search_read(self, model, domain, fields, limit, offset=0, *, summary=None, **kwargs):
        metadata = dict(operation='search_read', tool=self.tool, model=model, domain=domain,
                        field_count=len(fields), domain_term_count=len(domain), limit=limit, offset=offset,
                        active_test=(kwargs.get('context') or {}).get('active_test', True),
                        trace_context=self.arguments.get('trace_context', {}), span_id=self.span_id)
        append_trace(self.store, self.arguments['trace_id'], dict(metadata, event='odoo.query', status='running'))
        rows = self.client.search_read(model, domain, fields, limit, offset, **kwargs)
        append_trace(self.store, self.arguments['trace_id'],
                     dict(metadata, **(summary(rows) if summary else {}), event='odoo.read', status='ok',
                          count=len(rows), result_count=len(rows)))
        return rows

    def module_records(self, module=None, installed_only=False, offset=0):
        domain = [['name', '=', module]] if module else []
        if installed_only:
            domain.append(['state', '=', 'installed'])
        return self.search_read('ir.module.module', domain, ['name', 'state', 'latest_version'], 100, offset)
