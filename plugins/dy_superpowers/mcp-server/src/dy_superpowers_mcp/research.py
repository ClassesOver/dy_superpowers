"""Workspace-scoped, append-preserving module research records."""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
import unicodedata
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .storage import record_lock, atomic_write
from .memory import module_memory_path, validate_module

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


def _legacy_visible(record: dict[str, Any]) -> list[str]:
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


def _visible(record: dict[str, Any]) -> list[str]:
    payload = json.dumps(record, ensure_ascii=False, indent=2)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return ["<!-- dy-research-record:start -->",
            f"## {record['module']} / {record['config_item']}（{record['check_order']} + {record['research_id']}）",
            "```json", payload, "```", f"<!-- dy-research-record:sha256 {digest} -->",
            "<!-- dy-research-record:end -->"]


@dataclass
class _Block:
    text: str
    offset: int

    def group(self, index):
        return self.text

    def start(self):
        return self.offset

    def end(self):
        return self.offset + len(self.text)


def _blocks(text):
    # 逐行核对边界；不让正则跳过残块或吞掉嵌套记录。
    offset = 0
    start = None
    for line in text.splitlines(keepends=True):
        marker = line.rstrip("\r\n")
        if marker == "<!-- dy-research-record:start -->":
            if start is not None:
                raise ResearchIntegrityError(text[start:offset + len(line)])
            start = offset
        elif marker == "<!-- dy-research-record:end -->":
            if start is None:
                raise ResearchIntegrityError(line)
            yield _Block(text[start:offset + len(marker)], start)
            start = None
        elif marker.startswith("<!-- dy-research-record:") and start is None:
            raise ResearchIntegrityError(line)
        offset += len(line)
    if start is not None:
        raise ResearchIntegrityError(text[start:])


def _decode(block: str) -> dict[str, Any] | None:
    match = _DATA.search(block)
    try:
        if "\n```json\n" in block:
            payload = block.split("\n```json\n", 1)[1].split("\n```\n", 1)[0]
            value = json.loads(payload)
        else:
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
    if "\n```json\n" in block:
        if block != "\n".join(_visible(value)):
            raise ResearchIntegrityError(block)
        return
    visible = block.split("<!-- dy-research-record:data ", 1)[0]
    if visible != "\n".join(_legacy_visible(value)[:-2]) + "\n":
        raise ResearchIntegrityError(block)


def _matching(text: str, module: str, check_order: str, research_id: str,
              scope: str | None = None, scope_id: str | None = None):
    target = f"{module}:{check_order}:{research_id}"
    heading = re.compile(r"^## " + re.escape(module) + r" / .*（" + re.escape(check_order) + r" \+ " + re.escape(research_id) + r"）$", re.MULTILINE)
    for match in _blocks(text):
        block = match.group(0)
        value = _decode(block)
        identified = value is not None and all(isinstance(value.get(field), str) for field in ("module", "check_order", "research_id", "scope"))
        if not (identified and _key(value) == target) and not heading.search(block):
            if not identified:
                sys.stderr.write("存在无法识别的损坏调研记录，已保留；请核对当前模块 research.md。\n")
            continue
        if not identified:
            # 身份可从标题确定，但范围已不可解码，不能把带范围的查询当成缺失。
            yield match, value
            continue
        visible_scope = re.search(r"^- 适用范围：(.*)$", block, re.MULTILINE)
        visible_id = re.search(r"^- 范围标识：(.*)$", block, re.MULTILINE)
        if scope_id is not None:
            if not ((identified and _scope_id(value) == scope_id) or (visible_id and visible_id.group(1) == scope_id)):
                _validate_block(block, value)
                continue
        elif scope is not None:
            if not ((identified and _scope_text(value["scope"]) == _scope_text(scope))
                    or (visible_scope and _scope_text(visible_scope.group(1)) == _scope_text(scope))):
                _validate_block(block, value)
                continue
        yield match, value


def _snapshot_path(directory: Path, reference: str) -> Path:
    if not isinstance(reference, str) or not re.fullmatch(r"history/[a-f0-9]{64}\.md", reference):
        raise ValueError("调研历史快照引用不合法")
    path = directory / reference
    if path.parent.is_symlink() or path.is_symlink() or not path.resolve().is_relative_to(directory):
        raise ValueError("调研历史快照必须保存在当前工作区记忆目录")
    return path


def _archive_block(directory: Path, block: str) -> str:
    reference = f"history/{_revision(block)}.md"
    path = _snapshot_path(directory, reference)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text(encoding="utf-8") != block:
            raise ValueError("调研历史快照已损坏，请恢复备份后重试")
    else:
        atomic_write(path, block)
    return reference


def _repair_history(directory: Path, history: list) -> list[dict]:
    repaired = []
    for item in history:
        if not isinstance(item, dict):
            continue  # 不可解码的历史仍由本次完整原文快照保留。
        item = dict(item)
        original = item.pop("original_block", None)
        if isinstance(original, str):
            item["original_block_ref"] = _archive_block(directory, original)
            item["revision"] = _revision(original)
        repaired.append(item)
    return repaired


def _read_history(directory: Path, history: list[dict]) -> list[dict]:
    result = []
    for item in history:
        item = dict(item)
        if "original_block_ref" in item:
            try:
                path = _snapshot_path(directory, item["original_block_ref"])
                original = path.read_text(encoding="utf-8")
                if _revision(original) != path.stem:
                    raise ValueError("历史快照摘要不一致")
            except (OSError, ValueError) as exc:
                raise ValueError("调研历史快照缺失或损坏，请先恢复工作区记忆备份") from exc
            item["original_block"] = original
        result.append(item)
    return result


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
    if data["confirmed_at"] != "未提供":
        try:
            confirmed_at = datetime.fromisoformat(data["confirmed_at"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("confirmed_at 必须是 ISO 8601 日期或日期时间，或填写“未提供”") from exc
        now = datetime.now(timezone.utc)
        if "T" in data["confirmed_at"] or " " in data["confirmed_at"]:
            if confirmed_at.tzinfo is None:
                confirmed_at = confirmed_at.replace(tzinfo=timezone.utc)
            if confirmed_at.astimezone(timezone.utc) > now:
                raise ValueError("confirmed_at 不能晚于当前时间")
        elif confirmed_at.date() > now.date():
            raise ValueError("confirmed_at 不能晚于当前日期")
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
    module = validate_module(data["module"])
    if module in {"expense", "general-ledger"}:
        from .inspection import _matrix
        _, matrix_keys = _matrix(module)
        if (data["check_order"], data["research_id"]) not in matrix_keys:
            raise ValueError("检查键不在当前模块巡检矩阵中")
    path = module_memory_path(workspace, module)
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    with record_lock(path):
        existing = path.read_text(encoding="utf-8") if path.is_file() else "# 模块调研结论\n"
        if data.get("repair"):
            if data.get("expected_revision") is None:
                raise ValueError("修复调研必须提供读取时的 expected_revision")
            candidates = list(_matching(existing, data["module"], data["check_order"], data["research_id"]))
            matches = [(match, value) for match, value in candidates
                       if _revision(match.group(0)) == data["expected_revision"]]
            if not matches:
                raise ValueError("调研修订冲突，请重新读取、合并并确认")
            for match, value in candidates:
                if match.start() == matches[0][0].start():
                    continue
                _validate_block(match.group(0), value)
                if (_scope_text(value["scope"]) == _scope_text(data["scope"])
                        or (data.get("scope_id") and _scope_id(value) == data["scope_id"])):
                    raise ValueError("修复范围与其他记录冲突，请核对范围标识")
        else:
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
                history = _repair_history(directory, history)
                old = {"status": "invalidated", "original_block_ref": _archive_block(directory, match.group(0)),
                       "revision": _revision(match.group(0))}
            else:
                old = {key: value for key, value in prior.items() if key not in {"history", "updated_at"}}
                old["status"] = "superseded"
            history.append(old)
        record = {key: data[key] for key in _REQUIRED}
        prior_id = prior.get("scope_id") if prior else None
        if data.get("repair"):
            scope_id = data.get("scope_id") or (prior_id if isinstance(prior_id, str) and _KEY_PART.fullmatch(prior_id) else _scope_id(data))
        else:
            scope_id = data.get("scope_id") or (_scope_id(prior) if prior else _scope_id(data))
        record.update({"scope_id": scope_id,
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
    for name, value in (("scope", scope), ("scope_id", scope_id)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{name} 必须是非空文本")
    path = module_memory_path(workspace, module)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    candidates = list(_matching(text, module, check_order, research_id)) if scope is not None and scope_id is not None else None
    normalized_scope = _scope_text(scope) if scope is not None else None
    matches = ([(match, value) for match, value in candidates
                if value is not None and _scope_text(value.get("scope", "")) == normalized_scope
                and _scope_id(value) == scope_id]
               if candidates is not None else
               list(_matching(text, module, check_order, research_id, scope, scope_id)))
    if candidates is not None and not matches:
        corrupted = [match for match, value in candidates if value is None]
        if corrupted:
            # 损坏块与单参路径同样抛完整性错误，提示修复而不是误报缺失。
            _validate_block(corrupted[0].group(0), None)
        if any(value is not None and (_scope_text(value.get("scope", "")) == normalized_scope
                                     or _scope_id(value) == scope_id)
               for _, value in candidates):
            raise ValueError("scope 与 scope_id 不匹配")
    if len(matches) > 1:
        raise ValueError("该检查键有多个适用范围，请通过 scope 或 scope_id 明确选择")
    if not matches:
        return None
    match, value = matches[0]
    _validate_block(match.group(0), value)
    directory = path.parent
    history = _read_history(directory, value.get("history", []))
    return {**value, "history": history, "scope_id": _scope_id(value), "revision": _revision(match.group(0))}


def read_module_records(workspace: str | Path, module: str, *, offset: int = 0, limit: int = 100,
                        scope: str | None = None, scope_id: str | None = None) -> tuple[list[dict[str, Any]], bool]:
    module = validate_module(module)
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("offset 必须是非负整数")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("limit 必须是 1 到 100 的整数")
    for name, value in (("scope", scope), ("scope_id", scope_id)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{name} 必须是非空文本")
    path = module_memory_path(workspace, module)
    if not path.is_file():
        return [], False
    text = path.read_text(encoding="utf-8")
    records = []
    for block in _blocks(text):
        value = _decode(block.text)
        if value is None or value.get("module") != module:
            raise ResearchIntegrityError(block.text)
        _validate_block(block.text, value)
        records.append({**value, "history": _read_history(path.parent, value.get("history", [])),
                        "scope_id": _scope_id(value), "revision": _revision(block.text)})
    if scope is not None or scope_id is not None:
        normalized_scope = _scope_text(scope) if scope is not None else None
        filtered = [record for record in records
                    if (scope is None or _scope_text(record.get("scope", "")) == normalized_scope)
                    and (scope_id is None or record["scope_id"] == scope_id)]
        if not filtered and scope is not None and scope_id is not None and any(
            _scope_text(record.get("scope", "")) == normalized_scope or record["scope_id"] == scope_id
            for record in records
        ):
            raise ValueError("scope 与 scope_id 不匹配")
        records = filtered
    selected = records[offset:offset + limit]
    return selected, offset + limit < len(records)
