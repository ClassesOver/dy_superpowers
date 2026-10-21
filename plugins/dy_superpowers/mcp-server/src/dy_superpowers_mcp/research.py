"""Workspace-scoped, append-preserving module research records."""

from __future__ import annotations

import hashlib
import json
import unicodedata
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .storage import record_lock, atomic_write

_BLOCK = re.compile(r"^<!-- dy-research-record:start -->\n(?:(?!^<!-- dy-research-record:start -->).)*?^<!-- dy-research-record:end -->$", re.MULTILINE | re.DOTALL)
_DATA = re.compile(r"^<!-- dy-research-record:data (.*) -->$", re.MULTILINE)
_REQUIRED = ("module", "check_order", "research_id", "config_item", "conclusion", "scope", "confirmed_by", "confirmed_at")
_SENSITIVE = re.compile(r"(?i)(password|passwd|token|secret|api[_-]?key|员工身份证|手机号|银行卡)")
_KEY_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def _key(data: dict[str, Any]) -> str:
    return f"{data['module']}:{data['check_order']}:{data['research_id']}"


class ResearchIntegrityError(ValueError):
    """保留目标块版本，允许显式确认后修复。"""

    def __init__(self, block: str):
        self.revision = _revision(block)
        super().__init__(f"调研记录内容不一致或结构损坏；不得用于巡检。核对后使用 repair=true 和 expected_revision={self.revision} 确认修复")


def _revision(block: str) -> str:
    return hashlib.sha256(block.encode("utf-8")).hexdigest()


def _scope_text(scope: str) -> str:
    return re.sub(r"\s*/\s*", "/", unicodedata.normalize("NFKC", scope).strip())


def _scope_id(record: dict[str, Any]) -> str:
    return record.get("scope_id") or hashlib.sha256(_scope_text(record["scope"]).encode("utf-8")).hexdigest()[:16]


def _visible(record: dict[str, Any]) -> list[str]:
    lines = ["<!-- dy-research-record:start -->",
             f"## {record['module']} / {record['config_item']}（{record['check_order']} + {record['research_id']}）",
             f"- 调研结论：{record['conclusion']}", f"- 适用范围：{record['scope']}"]
    if "scope_id" in record:
        lines.append(f"- 范围标识：{record['scope_id']}")
    lines.extend([f"- 验证依据：{record.get('evidence_ref') or '待补充'}",
                  f"- 确认人/日期：{record['confirmed_by']} / {record['confirmed_at']}",
                  f"- 状态：{record['status']}"])
    if record.get("retirement_reason"):
        lines.append(f"- 停用原因：{record['retirement_reason']}")
    return [*lines, "<!-- dy-research-record:data " + json.dumps(record, ensure_ascii=False, separators=(",", ":")) + " -->",
            "<!-- dy-research-record:end -->"]


def _decode(block: str) -> dict[str, Any] | None:
    match = _DATA.search(block)
    try:
        value = json.loads(match.group(1)) if match else None
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _validate_block(block: str, value: dict[str, Any] | None) -> None:
    if (value is None or not all(isinstance(value.get(name), str) for name in (*_REQUIRED, "status"))
            or value["status"] not in {"confirmed", "retired"}
            or not isinstance(value.get("history", []), list)
            or not all(isinstance(item, dict) for item in value.get("history", []))):
        raise ResearchIntegrityError(block)
    visible = block.split("<!-- dy-research-record:data ", 1)[0]
    if visible != "\n".join(_visible(value)[:-2]) + "\n":
        raise ResearchIntegrityError(block)


def _matching(text: str, module: str, check_order: str, research_id: str,
              scope: str | None = None, scope_id: str | None = None):
    target = f"{module}:{check_order}:{research_id}"
    heading = re.compile(r"^## " + re.escape(module) + r" / .*（" + re.escape(check_order) + r" \+ " + re.escape(research_id) + r"）$", re.MULTILINE)
    for match in _BLOCK.finditer(text):
        block = match.group(0)
        value = _decode(block)
        identified = value is not None and all(isinstance(value.get(field), str) for field in ("module", "check_order", "research_id", "scope"))
        if not (identified and _key(value) == target) and not heading.search(block):
            continue
        visible_scope = re.search(r"^- 适用范围：(.*)$", block, re.MULTILINE)
        visible_id = re.search(r"^- 范围标识：(.*)$", block, re.MULTILINE)
        if scope_id is not None:
            if not ((identified and _scope_id(value) == scope_id) or (visible_id and visible_id.group(1) == scope_id)):
                continue
        elif scope is not None:
            if not ((identified and _scope_text(value["scope"]) == _scope_text(scope))
                    or (visible_scope and _scope_text(visible_scope.group(1)) == _scope_text(scope))):
                continue
        yield match, value


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
    status = data.get("status", "confirmed")
    if not isinstance(status, str) or status not in {"confirmed", "retired"}:
        raise ValueError("status 仅支持 confirmed 或 retired")
    if status == "retired" and (not isinstance(data.get("retirement_reason"), str) or not data["retirement_reason"].strip()):
        raise ValueError("停用范围必须提供停用原因 retirement_reason")
    if data.get("scope_id") is not None and (not isinstance(data["scope_id"], str) or _KEY_PART.fullmatch(data["scope_id"]) is None):
        raise ValueError("scope_id 必须是稳定的字母、数字、点、下划线或连字符标识")
    if data.get("repair", False) not in (False, True) or ("repair" in data and not isinstance(data["repair"], bool)):
        raise ValueError("repair 必须是布尔值")
    for name in ("evidence_ref", "retirement_reason", "expected_revision"):
        if data.get(name) is not None and not isinstance(data[name], str):
            raise ValueError(f"{name} 必须是文本")
    text_fields = (*_REQUIRED, "evidence_ref", "retirement_reason")
    if any("\n" in str(data.get(name, "")) or "\r" in str(data.get(name, "")) for name in text_fields):
        raise ValueError("调研字段不能包含换行")
    if any("<!-- dy-research-record:" in str(data.get(name, "")) for name in ("config_item", "conclusion", "scope", "evidence_ref", "retirement_reason")):
        raise ValueError("调研字段包含保留的记录标记")
    if len(data["conclusion"]) > 2000 or len(data["scope"]) > 500:
        raise ValueError("调研结论或适用范围超过长度限制")
    if any(_SENSITIVE.search(str(data.get(name, ""))) for name in ("conclusion", "scope", "evidence_ref", "retirement_reason")):
        raise ValueError("调研记录不得包含密码、令牌或敏感个人信息")
    if any(re.search(r"(?<!\d)(?:1[3-9]\d{9}|\d{17}[\dXx])(?!\d)", str(data.get(name, ""))) for name in ("conclusion", "scope", "evidence_ref", "retirement_reason")):
        raise ValueError("调研记录不得包含敏感个人信息")
    from .memory import WorkspaceMemory

    memory = WorkspaceMemory(workspace)
    directory, path = memory._paths("module-research.md")
    directory.mkdir(parents=True, exist_ok=True)
    with record_lock(path):
        existing = memory.read("module-research.md") or "# 模块调研结论\n"
        matches = list(_matching(existing, data["module"], data["check_order"], data["research_id"],
                                 scope=data["scope"], scope_id=data.get("scope_id")))
        # 稳定标识可用于重命名范围，但不能借新标识重复创建同一范围。
        if data.get("scope_id"):
            same_label = list(_matching(existing, data["module"], data["check_order"], data["research_id"], scope=data["scope"]))
            if any(match.start() not in {entry[0].start() for entry in matches} for match, _ in same_label):
                raise ValueError("范围文本已关联其他 scope_id，请沿用原范围标识")
        if len(matches) > 1:
            raise ValueError("该检查键存在重复范围记录，请核对范围标识")
        prior = None
        match = None
        if matches:
            match, prior = matches[0]
            if not data.get("repair"):
                _validate_block(match.group(0), prior)
            if data.get("expected_revision") is None:
                raise ValueError("修订已有调研必须提供读取时的 expected_revision")
            if data["expected_revision"] != _revision(match.group(0)):
                raise ValueError("调研修订冲突，请重新读取、合并并确认")
        elif data.get("expected_revision") is not None or data.get("repair"):
            raise ValueError("调研修订冲突：原记录不存在，不能修复或覆盖")
        history = list(prior.get("history", [])) if prior and isinstance(prior.get("history", []), list) else []
        if match:
            if data.get("repair"):
                old = {"status": "invalidated", "original_block": match.group(0), "revision": _revision(match.group(0))}
            else:
                old = {key: value for key, value in prior.items() if key not in {"history", "updated_at"}}
                old["status"] = "superseded"
            history.append(old)
        record = {key: data[key] for key in _REQUIRED}
        record.update({"scope_id": data.get("scope_id") or (_scope_id(prior) if prior else _scope_id(data)),
                       "evidence_ref": data.get("evidence_ref", ""), "status": status, "history": history,
                       "updated_at": datetime.now(timezone.utc).isoformat()})
        if status == "retired":
            record["retirement_reason"] = data["retirement_reason"]
        block = "\n".join(_visible(record))
        if match:
            content = existing[:match.start()] + block + existing[match.end():]
        else:
            content = existing.rstrip() + "\n\n" + block + "\n"
        atomic_write(path, content)
        return {"stored": True, "check_key": _key(data), "revision_count": len(history), "scope": record["scope"],
                "scope_id": record["scope_id"], "revision": _revision(block), "status": status}


def read_record(workspace: str | Path, module: str, check_order: str, research_id: str,
                scope: str | None = None, scope_id: str | None = None) -> dict[str, Any] | None:
    from .memory import WorkspaceMemory

    for name, value in (("scope", scope), ("scope_id", scope_id)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{name} 必须是非空文本")
    text = WorkspaceMemory(workspace).read("module-research.md")
    matches = list(_matching(text, module, check_order, research_id, scope, scope_id))
    if len(matches) > 1:
        raise ValueError("该检查键有多个适用范围，请通过 scope 或 scope_id 明确选择")
    if not matches:
        return None
    match, value = matches[0]
    _validate_block(match.group(0), value)
    return {**value, "scope_id": _scope_id(value), "revision": _revision(match.group(0))}
