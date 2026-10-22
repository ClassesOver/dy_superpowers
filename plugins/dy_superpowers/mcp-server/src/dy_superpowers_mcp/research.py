"""Workspace-scoped, append-preserving module research records."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .memory import WorkspaceMemory, atomic_write, file_lock

_BLOCK = re.compile(r"^<!-- dy-research-record:start -->\n.*?^<!-- dy-research-record:data (\{[^\n]*\}) -->\n^<!-- dy-research-record:end -->$", re.MULTILINE | re.DOTALL)
_REQUIRED = ("module", "check_order", "research_id", "config_item", "conclusion", "scope", "confirmed_by", "confirmed_at")
_SENSITIVE = re.compile(r"(?i)(password|passwd|token|secret|api[_-]?key|员工身份证|手机号|银行卡)")
_KEY_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def _key(data: dict[str, Any]) -> str:
    return f"{data['module']}:{data['check_order']}:{data['research_id']}"


def _load(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records = []
    for match in _BLOCK.finditer(path.read_text(encoding="utf-8")):
        try:
            value = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def write_record(workspace: str | Path, data: dict[str, Any]) -> dict[str, Any]:
    missing = [name for name in _REQUIRED if not isinstance(data.get(name), str) or not data[name].strip()]
    if missing:
        raise ValueError("调研记录缺少字段: " + ", ".join(missing))
    if data.get("confirmed") is not True:
        raise PermissionError("调研结论写入需要用户明确确认")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", data["module"]):
        raise ValueError("module 必须是小写模块标识")
    for name in ("check_order", "research_id"):
        if _KEY_PART.fullmatch(data[name]) is None:
            raise ValueError(f"{name} 必须是字母、数字、点、下划线或连字符组成的标识")
    try:
        datetime.fromisoformat(data["confirmed_at"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("confirmed_at 必须是 ISO 8601 日期或日期时间") from exc
    text_fields = (*_REQUIRED, "evidence_ref")
    if any("\n" in str(data.get(name, "")) or "\r" in str(data.get(name, "")) for name in text_fields):
        raise ValueError("调研字段不能包含换行")
    if any("<!-- dy-research-record:" in str(data.get(name, "")) for name in ("config_item", "conclusion", "scope", "evidence_ref")):
        raise ValueError("调研字段包含保留的记录标记")
    if len(data["conclusion"]) > 2000 or len(data["scope"]) > 500:
        raise ValueError("调研结论或适用范围超过长度限制")
    if any(_SENSITIVE.search(str(data.get(name, ""))) for name in ("conclusion", "scope", "evidence_ref")):
        raise ValueError("调研记录不得包含密码、令牌或敏感个人信息")
    if any(re.search(r"(?<!\d)(?:1[3-9]\d{9}|\d{17}[\dXx])(?!\d)", str(data.get(name, ""))) for name in ("conclusion", "scope", "evidence_ref")):
        raise ValueError("调研记录不得包含敏感个人信息")
    memory = WorkspaceMemory(workspace)
    directory, path = memory._paths("module-research.md")
    directory.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        records = _load(path)
        current_key = _key(data)
        prior = next((item for item in reversed(records) if _key(item) == current_key), None)
        history = list(prior.get("history", [])) if prior else []
        if prior:
            old = {key: value for key, value in prior.items() if key not in {"history", "updated_at"}}
            old["status"] = "superseded"
            history.append(old)
        record = {key: data[key] for key in _REQUIRED}
        record.update({"evidence_ref": data.get("evidence_ref", ""), "status": "confirmed", "history": history, "updated_at": datetime.now(UTC).isoformat()})
        existing = memory.read("module-research.md") or "# 模块调研结论\n"
        def remove_current(match):
            try:
                value = json.loads(match.group(1))
            except json.JSONDecodeError:
                return match.group(0)
            return "" if _key(value) == current_key else match.group(0)
        existing = _BLOCK.sub(remove_current, existing)
        visible = ["<!-- dy-research-record:start -->", f"## {record['module']} / {record['config_item']}（{record['check_order']} + {record['research_id']}）", f"- 调研结论：{record['conclusion']}", f"- 适用范围：{record['scope']}", f"- 验证依据：{record['evidence_ref'] or '待补充'}", f"- 确认人/日期：{record['confirmed_by']} / {record['confirmed_at']}", f"- 状态：{record['status']}", "<!-- dy-research-record:data " + json.dumps(record, ensure_ascii=False, separators=(",", ":")) + " -->", "<!-- dy-research-record:end -->"]
        atomic_write(path, existing.rstrip() + "\n\n" + "\n".join(visible) + "\n")
        return {"stored": True, "check_key": current_key, "revision_count": len(history)}


def read_record(workspace: str | Path, module: str, check_order: str, research_id: str) -> dict[str, Any] | None:
    target = f"{module}:{check_order}:{research_id}"
    _, path = WorkspaceMemory(workspace)._paths("module-research.md")
    return next((item for item in reversed(_load(path)) if _key(item) == target), None)
