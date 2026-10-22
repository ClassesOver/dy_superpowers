"""受限的 Odoo 12 JSON-RPC 只读适配器（使用 Odoo 内置 /jsonrpc 端点，仅依赖标准库）。"""

from __future__ import annotations

import itertools
import json
import os
import re
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

DEFAULT_TIMEOUT_SECONDS = 15
MAX_TIMEOUT_SECONDS = 600
# 客户端层面的只读白名单：即使上层校验被绕过，也不会发出写方法。
READ_METHODS = frozenset({"search_read", "fields_get"})
_MODEL_NAME = re.compile(r"^[a-z0-9_.]{1,128}$")
_MODULE_NAME = re.compile(r"^[a-z0-9_]{1,128}$")
# 任何模型上都禁止读取或筛选的字段：凭据、证件号、银行账户、生日和私人信息。
_SENSITIVE_FIELD = re.compile(r"password|passwd|token|secret|api_key|totp|signature|identification_id|passport_id|ssnid|sinid|bank_account|acc_number|birthday|place_of_birth|^private_")
# 人员相关模型上的通讯与住址字段；经关联字段跳转到其他模型时同样拦截。
_PERSON_MODELS = frozenset({"hr.employee", "hr.employee.public", "hr.applicant", "res.users", "res.partner"})
_CONTACT_FIELD = re.compile(r"(^|_)(email|phone|mobile|street2?|zip|city)(_|$)")

# (endpoint, JSON-RPC 请求体, 超时秒数) -> 解码后的 JSON-RPC 响应
Transport = Callable[[str, dict[str, Any], float], Any]


class OdooConfigurationError(ValueError):
    """连接配置缺失或不合法。"""


class OdooRPCError(RuntimeError):
    """远程连接、认证或 Odoo 调用失败，消息不包含远端正文。"""


@dataclass(frozen=True)
class OdooSettings:
    url: str
    database: str
    username: str
    password: str

    @classmethod
    def from_environment(cls) -> OdooSettings:
        # 环境变量名 = 字段名大写加 ODOO_ 前缀（database 特例为 ODOO_DB）。
        names = {"url": "ODOO_URL", "database": "ODOO_DB"}
        names.update({field: f"ODOO_{field.upper()}" for field in ("username", "password")})
        values = {field: os.environ.get(env_name, "").strip() for field, env_name in names.items()}
        missing = [env_name for field, env_name in names.items() if not values[field]]
        if missing:
            raise OdooConfigurationError("缺少 Odoo 连接配置: " + ", ".join(missing))
        return cls(**values)


def odoo_timeout() -> float:
    """Odoo 请求超时秒数，默认 15 秒，可用 DY_ODOO_TIMEOUT 覆盖。"""
    raw = os.environ.get("DY_ODOO_TIMEOUT", "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError as exc:
        raise OdooConfigurationError("DY_ODOO_TIMEOUT 必须是数字") from exc
    if not 0 < value <= MAX_TIMEOUT_SECONDS:
        raise OdooConfigurationError(f"DY_ODOO_TIMEOUT 必须在 0 到 {MAX_TIMEOUT_SECONDS} 秒之间")
    return value


def urllib_transport(endpoint: str, payload: dict[str, Any], timeout: float) -> Any:
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as handle:  # noqa: S310 - 协议已限定为 http/https
        return json.loads(handle.read().decode("utf-8"))


class _RemoteError(RuntimeError):
    """内部错误：远端返回 error 或格式无效；对外统一转换为不含远端正文的 OdooRPCError。"""


def validate_model(model: object) -> str:
    if not isinstance(model, str) or _MODEL_NAME.fullmatch(model) is None:
        raise OdooConfigurationError("model 必须是合法的 Odoo 模型名")
    return model


def _sensitive_segments(model: str, path: str) -> list[str]:
    segments = path.split(".")
    blocked = [segment for segment in segments if _SENSITIVE_FIELD.search(segment)]
    for index, segment in enumerate(segments):
        if (index > 0 or model in _PERSON_MODELS) and _CONTACT_FIELD.search(segment):
            blocked.append(segment)
    return blocked


def check_field_access(model: str, fields: object, domain: list[Any]) -> list[str]:
    """要求显式列出字段，并拦截返回字段和 domain 条件中的敏感字段。"""
    if not isinstance(fields, list) or not fields or not all(isinstance(field, str) and field for field in fields):
        raise OdooConfigurationError("fields 必须显式列出需要的字段（只读取最小必要字段）")
    paths = list(fields)
    for leaf in domain:
        if isinstance(leaf, list) and len(leaf) == 3 and isinstance(leaf[0], str):
            paths.append(leaf[0])
    blocked = sorted({segment for path in paths for segment in _sensitive_segments(model, path)})
    if blocked:
        raise OdooConfigurationError("禁止读取或筛选敏感字段: " + ", ".join(blocked))
    return fields


def validate_module(module: object) -> str:
    if not isinstance(module, str) or _MODULE_NAME.fullmatch(module) is None:
        raise OdooConfigurationError("module 必须是合法的 Odoo 模块技术名")
    return module


class OdooReadClient:
    """只暴露只读查询，连接参数来自环境变量。"""

    def __init__(self, settings: OdooSettings, transport: Transport | None = None) -> None:
        base = settings.url.rstrip("/")
        parts = urlsplit(base)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise OdooConfigurationError("ODOO_URL 必须是 http 或 https 地址")
        self.settings = settings
        self._endpoint = f"{base}/jsonrpc"
        self._timeout = odoo_timeout()
        self._transport = transport or urllib_transport
        self._request_ids = itertools.count(1)
        try:
            self.uid = self._call("common", "authenticate", [settings.database, settings.username, settings.password, {}])
        except Exception as exc:  # noqa: BLE001 - 远端错误与网络错误统一脱敏
            raise OdooRPCError("Odoo 认证失败") from exc
        if not isinstance(self.uid, int) or isinstance(self.uid, bool) or self.uid <= 0:
            raise OdooRPCError("Odoo 认证失败")

    def _call(self, service: str, method: str, args: list[Any]) -> Any:
        request_id = next(self._request_ids)
        payload = {"jsonrpc": "2.0", "method": "call", "params": {"service": service, "method": method, "args": args}, "id": request_id}
        body = self._transport(self._endpoint, payload, self._timeout)
        if not isinstance(body, dict) or body.get("id") != request_id:
            raise _RemoteError("invalid response")
        if "error" in body or "result" not in body:
            raise _RemoteError("remote error")
        return body["result"]

    def _execute_kw(self, model: str, method: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        if method not in READ_METHODS:
            raise OdooRPCError("仅允许 Odoo 只读方法")
        return self._call("object", "execute_kw", [self.settings.database, self.uid, self.settings.password, model, method, args, kwargs])

    def search_read(self, model: str, domain: list[Any], fields: list[str], limit: int, offset: int = 0) -> list[dict[str, Any]]:
        try:
            result = self._execute_kw(model, "search_read", [domain], {"fields": fields, "limit": limit, "offset": offset})
        except Exception as exc:  # noqa: BLE001
            raise OdooRPCError("Odoo 只读请求失败") from exc
        if not isinstance(result, list):
            raise OdooRPCError("Odoo 返回格式无效")
        return result

    def read_records(self, model: str, ids: list[int], fields: list[str]) -> list[dict[str, Any]]:
        return self.search_read(model, [["id", "in", ids]], fields, min(len(ids), 100))

    def fields_get(self, model: str) -> dict[str, Any]:
        try:
            result = self._execute_kw(model, "fields_get", [], {"attributes": ["string", "type", "required", "readonly", "relation", "store"]})
        except Exception as exc:  # noqa: BLE001
            raise OdooRPCError("Odoo 字段元数据读取失败") from exc
        if not isinstance(result, dict):
            raise OdooRPCError("Odoo 返回格式无效")
        return result

    def module_records(self, module: str | None = None, installed_only: bool = False, offset: int = 0) -> list[dict[str, Any]]:
        domain: list[Any] = []
        if module:
            domain.append(["name", "=", module])
        if installed_only:
            domain.append(["state", "=", "installed"])
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise OdooConfigurationError("offset 必须是不小于 0 的整数")
        return self.search_read("ir.module.module", domain, ["name", "shortdesc", "latest_version", "state", "depends"], 100, offset)


def validate_read_arguments(arguments: dict[str, Any]) -> tuple[str, list[Any], list[str], int, int]:
    method = arguments.get("method", "search_read")
    if method != "search_read":
        raise OdooConfigurationError("dy_odoo_read 仅支持 read-only 方法 search_read")
    model = validate_model(arguments.get("model"))
    args = arguments.get("args", [[]])
    if not isinstance(args, list) or len(args) > 1 or (args and not isinstance(args[0], list)):
        raise OdooConfigurationError("args 仅允许包含一个 domain 列表")
    domain = args[0] if args else []
    kwargs = arguments.get("kwargs", {})
    if not isinstance(kwargs, dict):
        raise OdooConfigurationError("kwargs 必须是对象")
    fields = check_field_access(model, kwargs.get("fields"), domain)
    limit = kwargs.get("limit", 100)
    offset = kwargs.get("offset", 0)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 100:
        raise OdooConfigurationError("kwargs.limit 必须是 1 到 100 的整数")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise OdooConfigurationError("kwargs.offset 必须是不小于 0 的整数")
    return model, domain, fields, limit, offset
