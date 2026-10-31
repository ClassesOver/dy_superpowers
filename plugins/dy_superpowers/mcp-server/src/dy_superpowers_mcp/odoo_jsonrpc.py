"""受限的 Odoo 12 JSON-RPC 只读适配器。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from threading import RLock
from typing import Any

from .endpoints import validate_endpoint


class OdooConfigurationError(ValueError):
    """连接配置缺失或不合法。"""


class OdooRPCError(RuntimeError):
    """远程连接、认证或 Odoo 调用失败，消息不包含远端正文。"""

    def __init__(self, message, *, category=None, request_id=None):
        super().__init__(message)
        allowed = {'权限拒绝', '认证拒绝', '业务校验失败', '业务操作被拒绝', '记录不存在或不可访问', '服务端错误'}
        self.diagnostic = ({'category': category, 'request_id': request_id}
                           if isinstance(category, str) and category in allowed
                           and type(request_id) is int and request_id > 0 else None)


@dataclass(frozen=True)
class OdooSettings:
    url: str
    database: str
    username: str
    password: str

    def __post_init__(self):
        try:
            validate_endpoint(self.url, "APP_URL")
        except ValueError as exc:
            raise OdooConfigurationError(str(exc)) from exc

    @classmethod
    def from_environment(cls) -> "OdooSettings":
        names = {"url": "APP_URL", "database": "DB_NAME", "username": "APP_USER", "password": "APP_PASSWORD"}
        values = {field: os.environ.get(env_name, "") for field, env_name in names.items()}
        for field in ("url", "database", "username"):
            values[field] = values[field].strip()
        missing = [env_name for field, env_name in names.items() if not values[field]]
        if missing:
            raise OdooConfigurationError("缺少 Odoo 连接配置: " + ", ".join(missing))
        return cls(**values)


class OdooReadClient:
    """受限的查询、精确读取和元数据接口，连接参数来自环境变量。"""

    def __init__(self, settings: OdooSettings, session_factory=None) -> None:
        base = settings.url.rstrip("/")
        self.settings = settings
        self._rpc_url = f"{base}/jsonrpc"
        if session_factory is None:
            import requests

            session_factory = requests.Session
        self._session = session_factory()
        self._session.trust_env = False
        self._lock = RLock()
        self._request_id = 0
        try:
            self.uid = self._rpc("common", "login", [settings.database, settings.username, settings.password])
        except Exception as exc:
            self.close()
            raise OdooRPCError("Odoo 认证失败") from exc
        if not isinstance(self.uid, int) or isinstance(self.uid, bool) or self.uid <= 0:
            self.close()
            raise OdooRPCError("Odoo 认证失败")

    def close(self):
        self._session.close()

    def _rpc(self, service: str, method: str, args: list[Any]) -> Any:
        with self._lock:
            self._request_id += 1
            request_id = self._request_id
            request = {
                "jsonrpc": "2.0",
                "method": "call",
                "params": {"service": service, "method": method, "args": args},
                "id": request_id,
            }
            try:
                response = self._session.post(
                    self._rpc_url,
                    json=request,
                    timeout=20,
                    allow_redirects=False,
                )
                response.raise_for_status()
                payload = response.json()
            except OdooRPCError:
                raise
            except Exception as exc:
                raise OdooRPCError("Odoo JSON-RPC 请求失败") from exc
            if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
                raise OdooRPCError("Odoo JSON-RPC 返回格式无效")
            if "error" in payload:
                # 错误响应优先判定：部分错误响应不回显请求 id，不能误报为格式无效。
                error = payload['error']
                data = error.get('data') if isinstance(error, dict) else None
                name = data.get('name') if isinstance(data, dict) else None
                categories = {'odoo.exceptions.AccessError': '权限拒绝',
                              'odoo.exceptions.AccessDenied': '认证拒绝',
                              'odoo.exceptions.ValidationError': '业务校验失败',
                              'odoo.exceptions.UserError': '业务操作被拒绝',
                              'odoo.exceptions.MissingError': '记录不存在或不可访问'}
                category = categories.get(name, '服务端错误') if isinstance(name, str) else '服务端错误'
                # 只输出本地类别和本地生成的请求编号，不回显远端消息、类名或 traceback。
                raise OdooRPCError(f"Odoo JSON-RPC 调用失败：{category}（请求 {request_id}）",
                                   category=category, request_id=request_id)
            if payload.get("id") != request_id or "result" not in payload:
                raise OdooRPCError("Odoo JSON-RPC 返回格式无效")
            return payload["result"]

    def _execute_kw(self, model: str, method: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        return self._rpc("object", "execute_kw", [
            self.settings.database,
            self.uid,
            self.settings.password,
            model,
            method,
            args,
            kwargs,
        ])

    def search_read(self, model: str, domain: list[Any], fields: list[str], limit: int, offset: int = 0,
                    *, context: dict[str, bool] | None = None) -> list[dict[str, Any]]:
        options = {"fields": fields, "limit": limit, "offset": offset}
        if context is not None:
            _validate_search_context(model, context)
            options["context"] = context
        try:
            result = self._execute_kw(model, "search_read", [domain], options)
        except OdooRPCError:
            raise
        except Exception as exc:
            raise OdooRPCError("Odoo 只读请求失败") from exc
        if not isinstance(result, list):
            raise OdooRPCError("Odoo 返回格式无效")
        return result

    def read_records(self, model: str, ids: list[int], fields: list[str]) -> list[dict[str, Any]]:
        result = self._execute_kw(model, "read", [ids], {"fields": fields})
        if (not isinstance(result, list)
                or any(not isinstance(row, dict) or type(row.get('id')) is not int or row['id'] not in ids for row in result)
                or len({row['id'] for row in result}) != len(result)):
            raise OdooRPCError("Odoo 返回格式无效")
        return result

    def fields_get(self, model: str) -> dict[str, Any]:
        try:
            result = self._execute_kw(model, "fields_get", [], {"attributes": ["string", "type", "required", "readonly", "relation", "store", "selection"]})
        except OdooRPCError:
            raise
        except Exception as exc:
            raise OdooRPCError("Odoo 字段元数据读取失败") from exc
        if not isinstance(result, dict):
            raise OdooRPCError("Odoo 返回格式无效")
        return result

    def module_records(self, module: str | None = None, installed_only: bool = False, offset: int = 0) -> list[dict[str, Any]]:
        domain: list[Any] = []
        if module is not None:
            if not isinstance(module, str) or not module.strip():
                raise OdooConfigurationError("module 必须是非空模块名")
            domain.append(["name", "=", module.strip()])
        if installed_only:
            domain.append(["state", "=", "installed"])
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise OdooConfigurationError("offset 必须是不小于 0 的整数")
        return self.search_read("ir.module.module", domain, ["name", "shortdesc", "latest_version", "state", "dependencies_id"], 100, offset)


def _validate_search_context(model: str, context: Any) -> None:
    allowed = {'active_test', 'view_company'} if model == 'res.company' else {'active_test'}
    if not isinstance(context, dict) or set(context) - allowed or any(type(value) is not bool for value in context.values()):
        raise OdooConfigurationError('context 仅支持布尔型 active_test；res.company 另支持布尔型 view_company')


def validate_read_arguments(arguments: dict[str, Any]) -> tuple[str, list[Any], list[str], int, int, dict[str, bool]]:
    method = arguments.get("method", "search_read")
    if method != "search_read":
        raise OdooConfigurationError("仅支持 search_read 只读查询")
    model = arguments.get("model")
    if not isinstance(model, str) or not model or len(model) > 128 or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789_." for ch in model):
        raise OdooConfigurationError("model 必须是合法的 Odoo 模型名")
    args = arguments.get("args", [[]])
    if not isinstance(args, list) or len(args) > 1 or (args and not isinstance(args[0], list)):
        raise OdooConfigurationError("args 仅允许包含一个 domain 列表")
    domain = args[0] if args else []
    kwargs = arguments.get("kwargs", {})
    if not isinstance(kwargs, dict):
        raise OdooConfigurationError("kwargs 必须是对象")
    if set(kwargs) - {"fields", "limit", "offset", "context"}:
        raise OdooConfigurationError("kwargs 仅支持 fields/limit/offset/context")
    context = kwargs.get("context", {})
    _validate_search_context(model, context)
    fields = kwargs.get("fields", [])
    if not isinstance(fields, list) or not all(isinstance(field, str) and field for field in fields):
        raise OdooConfigurationError("kwargs.fields 必须是字符串列表")
    limit = kwargs.get("limit", 100)
    offset = kwargs.get("offset", 0)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 100:
        raise OdooConfigurationError("kwargs.limit 必须是 1 到 100 的整数")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise OdooConfigurationError("kwargs.offset 必须是不小于 0 的整数")
    return model, domain, fields, limit, offset, context
