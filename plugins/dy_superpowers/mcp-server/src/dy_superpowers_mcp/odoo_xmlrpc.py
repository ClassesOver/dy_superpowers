"""受限的 Odoo 12 XML-RPC 只读适配器。"""

from __future__ import annotations

import os
import xmlrpc.client
from dataclasses import dataclass
from typing import Any


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
    def from_environment(cls) -> "OdooSettings":
        names = {
            "url": "ODOO_URL",
            "database": "ODOO_DB",
            "username": "ODOO_USERNAME",
            "password": "ODOO_PASSWORD",
        }
        values = {field: os.environ.get(env_name, "").strip() for field, env_name in names.items()}
        missing = [env_name for field, env_name in names.items() if not values[field]]
        if missing:
            raise OdooConfigurationError("缺少 Odoo 连接配置: " + ", ".join(missing))
        return cls(**values)


class OdooReadClient:
    """只暴露 search_read，连接参数来自环境变量。"""

    def __init__(self, settings: OdooSettings, proxy_factory=xmlrpc.client.ServerProxy) -> None:
        base = settings.url.rstrip("/")
        self.settings = settings
        self._common = proxy_factory(f"{base}/xmlrpc/2/common", allow_none=True)
        self._object = proxy_factory(f"{base}/xmlrpc/2/object", allow_none=True)
        try:
            self.uid = self._common.authenticate(settings.database, settings.username, settings.password, {})
        except Exception as exc:  # xmlrpc.client.Fault and transport errors are intentionally redacted
            raise OdooRPCError("Odoo 认证失败") from exc
        if not isinstance(self.uid, int) or self.uid <= 0:
            raise OdooRPCError("Odoo 认证失败")

    def search_read(self, model: str, domain: list[Any], fields: list[str], limit: int, offset: int = 0) -> list[dict[str, Any]]:
        try:
            result = self._object.execute_kw(
                self.settings.database,
                self.uid,
                self.settings.password,
                model,
                "search_read",
                [domain],
                {"fields": fields, "limit": limit, "offset": offset},
            )
        except Exception as exc:
            raise OdooRPCError("Odoo 只读请求失败") from exc
        if not isinstance(result, list):
            raise OdooRPCError("Odoo 返回格式无效")
        return result

    def read_records(self, model: str, ids: list[int], fields: list[str]) -> list[dict[str, Any]]:
        return self.search_read(model, [["id", "in", ids]], fields, min(len(ids), 100))

    def fields_get(self, model: str) -> dict[str, Any]:
        try:
            result = self._object.execute_kw(self.settings.database, self.uid, self.settings.password, model, "fields_get", [], {"attributes": ["string", "type", "required", "readonly", "relation", "store"]})
        except Exception as exc:
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
    fields = kwargs.get("fields", [])
    if not isinstance(fields, list) or not all(isinstance(field, str) and field for field in fields):
        raise OdooConfigurationError("kwargs.fields 必须是字符串列表")
    limit = kwargs.get("limit", 100)
    offset = kwargs.get("offset", 0)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 100:
        raise OdooConfigurationError("kwargs.limit 必须是 1 到 100 的整数")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise OdooConfigurationError("kwargs.offset 必须是不小于 0 的整数")
    return model, domain, fields, limit, offset
