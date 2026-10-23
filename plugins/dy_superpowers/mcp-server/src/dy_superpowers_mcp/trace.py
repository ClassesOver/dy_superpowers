"""Minimal structured trace storage with redaction and workspace isolation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import logging
from logging.handlers import RotatingFileHandler

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .storage import atomic_write, record_lock

SENSITIVE_PARTS = ("password", "passwd", "token", "secret", "api_key", "apikey", "credential")
DOMAIN_PRIVATE_PARTS = (
    "employee", "partner", "email", "phone", "mobile", "vat", "ssn", "passport",
    "identification", "birthday", "salary", "wage", "bank",
)
REDACTED = "[REDACTED]"
TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
TRACE_FILE_PATTERN = re.compile(r"^(?P<trace_id>[A-Za-z0-9_-]{1,128})(?:\.(?P<day>\d{4}-\d{2}-\d{2}))?\.jsonl(?P<backup>\.1)?$")
EVENT_FIELDS = {
    "event", "span_id", "parent_span_id", "operation", "tool", "status", "workspace_id",
    "duration_ms", "error_type", "error_code", "project_id", "target_id", "plan_version", "source_ref", "memory_file",
    "skill", "check_order", "research_id", "result", "evidence_ref",
    "model", "module", "count", "result_count", "content_bytes", "backend", "has_more",
    "field_count", "domain_term_count", "limit", "offset", "domain", "domain_truncated",
    "found", "history_count", "requested_count", "revision_count",
    "memory_content", "research_content", "query", "content_truncated",
    "workflow_id", "call_id", "otel_trace_id", "otel_span_id", "category",
    "check_key", "scope_id", "revision", "research_status", "trace_context", "valid", "error_count", "expected_count", "report_id",
}
SECRET_VALUE_PATTERN = re.compile(
    r"(?i)((?:password|passwd|token|secret|api[_-]?key|credential|confirmed[_ -]?by|"
    r"employee[_ -]?(?:name|id)|(?:work_)?email|(?:work_)?phone|mobile|vat|ssn|passport|"
    r"identification(?:_id)?|birthday|salary|wage|bank_account)\s*[:=]\s*)([^\r\n,;]+)|"
    r"(Bearer\s+)([A-Za-z0-9._~+/=-]+)"
)
TRACE_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_.:/#-]{1,256}$")
TRACE_CONTEXT_FIELDS = {"skill", "module", "check_order", "research_id", "scope_id", "revision", "environment"}
TRACE_RESULTS = {"通过", "部分通过", "不通过", "不适用", "待确认", "未检查", "未知", "ok", "error"}

MAX_TRACE_DOMAIN_CHARS = 2048
MAX_TRACE_CONTENT_BYTES = 65536


def workspace_id_for(workspace: str | Path) -> str:
    return hashlib.sha256(str(Path(workspace).resolve()).encode()).hexdigest()[:16]


def workflow_id_for(workspace: str | Path, trace_id: str) -> str:
    return hashlib.sha256(f"{Path(workspace).resolve()}\0{trace_id}".encode()).hexdigest()[:32]


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: REDACTED if any(part in key.lower() for part in SENSITIVE_PARTS + DOMAIN_PRIVATE_PARTS + ("confirmed_by",)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return SECRET_VALUE_PATTERN.sub(lambda match: f"{match.group(1) or match.group(3)}{REDACTED}", value)
    return value


def sanitize_trace_event(event: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise TypeError("trace event must be an object")
    safe_event = {}
    for key, value in event.items():
        if key not in EVENT_FIELDS:
            continue
        if key == "domain":
            serialized = _sanitize_domain(value, event.get("model"))
            if serialized is not None:
                safe_event[key] = serialized
            else:
                safe_event["domain_truncated"] = True
            continue
        if key == "trace_context":
            if isinstance(value, dict):
                safe_context = {context_key: redact(context_value) for context_key, context_value in value.items()
                                if context_key in TRACE_CONTEXT_FIELDS and isinstance(context_value, str)
                                and TRACE_CODE_PATTERN.fullmatch(context_value)}
                safe_event[key] = safe_context
            continue
        if key in {"memory_content", "research_content", "query"}:
            content, truncated = _sanitize_trace_content(key, value)
            if content is not None:
                safe_event[key] = content
                if truncated:
                    safe_event["content_truncated"] = True
            continue
        if isinstance(value, str):
            value = redact(value)
            if key == "result":
                if value not in TRACE_RESULTS:
                    continue
            elif not TRACE_CODE_PATTERN.fullmatch(value):
                continue
            safe_event[key] = value
        elif isinstance(value, (int, float, bool)):
            safe_event[key] = value
    return safe_event


def _sanitize_trace_content(key: str, value: Any) -> tuple[str | None, bool]:
    if key == "research_content" and isinstance(value, (dict, list)):
        value = redact(value)
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    elif isinstance(value, str):
        text = redact(value)
    else:
        return None, False
    encoded = text.encode("utf-8")
    if len(encoded) <= MAX_TRACE_CONTENT_BYTES:
        return text, False
    return encoded[:MAX_TRACE_CONTENT_BYTES].decode("utf-8", errors="ignore"), True


def _sanitize_domain(value: Any, model: Any = None) -> str | None:
    if not isinstance(value, list):
        return None
    budget = [100]

    def clean(item: Any, depth: int = 0, private: bool = False) -> Any:
        budget[0] -= 1
        if budget[0] < 0 or depth > 8:
            raise ValueError("domain exceeds trace limits")
        if isinstance(item, list):
            if len(item) == 3 and isinstance(item[0], str) and isinstance(item[1], str):
                field = item[0].lower()
                private_value = (
                    private
                    or any(part in field for part in SENSITIVE_PARTS + DOMAIN_PRIVATE_PARTS)
                    or (field == "name" and isinstance(model, str)
                        and model in {"hr.employee", "hr.contract", "res.partner", "res.users"})
                )
                return [clean(item[0], depth + 1), clean(item[1], depth + 1),
                        REDACTED if private_value else clean(item[2], depth + 1)]
            return [clean(value, depth + 1, private) for value in item]
        if isinstance(item, dict):
            return {
                str(key): REDACTED if any(part in str(key).lower() for part in SENSITIVE_PARTS)
                else clean(value, depth + 1, private)
                for key, value in item.items()
            }
        if isinstance(item, str):
            return redact(item)
        if item is None or isinstance(item, (bool, int, float)):
            return REDACTED if private else item
        raise ValueError("domain contains a non-JSON value")

    try:
        serialized = json.dumps(clean(value), ensure_ascii=True, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError):
        return None
    return serialized if len(serialized) <= MAX_TRACE_DOMAIN_CHARS else None


class TraceStore:
    def __init__(self, workspace: str | Path, root: str | Path | None = None, *, max_bytes: int = 100 * 1024 * 1024, max_age_days: int = 30) -> None:
        if max_bytes < 256 or max_age_days < 1:
            raise ValueError("日志容量至少 256 字节、保留期至少 1 天")
        self.max_bytes = max_bytes
        self.max_age_days = max_age_days
        self.workspace = Path(workspace).resolve()
        codex_root = Path(root or os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        self.workspace_id = workspace_id_for(self.workspace)
        self.directory = codex_root / "dy_superpowers" / "traces" / self.workspace_id

    def append(self, trace_id: str, event: dict[str, Any]) -> Path:
        if not TRACE_ID_PATTERN.fullmatch(trace_id):
            raise ValueError("trace_id must contain 1-128 letters, digits, underscores, or hyphens")
        self.directory.mkdir(parents=True, exist_ok=True)
        safe_event = sanitize_trace_event(event)
        now = datetime.now(timezone.utc)
        record = redact({**safe_event, "workspace_id": self.workspace_id,
                         "workflow_id": workflow_id_for(self.workspace, trace_id),
                         "trace_id": trace_id, "timestamp": now.isoformat()})
        path = self.directory / f"{trace_id}.{now.date().isoformat()}.jsonl"
        line = json.dumps(record, ensure_ascii=True, sort_keys=True)
        if len(line.encode("utf-8")) + 1 > self.max_bytes // 2:
            raise OSError("单条追踪事件超过日志容量限制")
        with record_lock(self.directory / ".retention"):
            handler = RotatingFileHandler(path, maxBytes=min(5 * 1024 * 1024, self.max_bytes // 2),
                                          backupCount=1, encoding="utf-8", delay=True)
            try:
                entry = logging.LogRecord("dy.trace", logging.INFO, "", 0, line, (), None)
                if handler.shouldRollover(entry):
                    handler.doRollover()
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            finally:
                handler.close()
            self._prune()
        return path

    def _prune_legacy(self, path: Path, oldest_day: date):
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        retained = []
        for line in lines:
            if not line.strip():
                continue
            try:
                timestamp = datetime.fromisoformat(json.loads(line)["timestamp"].replace("Z", "+00:00"))
                if timestamp.tzinfo is None:
                    raise ValueError("旧日志时间必须包含时区")
            except (ValueError, KeyError, TypeError, AttributeError):
                # 中断写入可能留下残行；保留原文供恢复，不能阻断其他追踪。
                if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).date() < oldest_day:
                    path.unlink()
                    return False
                sys.stderr.write("旧追踪日志含损坏行，已保留原文；请恢复备份，其他追踪继续。\n")
                return True
            if timestamp.astimezone(timezone.utc).date() >= oldest_day:
                retained.append(line)
        if not retained:
            path.unlink()
            return False
        if retained != lines:
            atomic_write(path, "".join(retained))
        return True

    def _prune(self):
        files = []
        oldest_day = datetime.now(timezone.utc).date() - timedelta(days=self.max_age_days - 1)
        for path in self.directory.iterdir():
            match = TRACE_FILE_PATTERN.fullmatch(path.name)
            if match is None or path.is_symlink():
                continue
            if not path.is_file():
                continue
            try:
                day = match.group("day")
                if day is not None:
                    if date.fromisoformat(day) < oldest_day:
                        path.unlink()
                        continue
                elif not self._prune_legacy(path, oldest_day):
                    continue
            except ValueError:
                # 包含 UTF-8 解码和文件名日期错误，按 mtime 清理并继续其他文件。
                sys.stderr.write("追踪日志编码或日期损坏，退回按文件时间清理；请恢复备份，其他追踪继续。\n")
                if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).date() < oldest_day:
                    path.unlink()
                    continue
            stat = path.stat()
            files.append((stat.st_mtime_ns, path.name, path, stat.st_size))
        total = sum(entry[3] for entry in files)
        for _, _, path, size in sorted(files):
            if total <= self.max_bytes:
                break
            path.unlink()
            total -= size

    def read(self, trace_id: str) -> list[dict[str, Any]]:
        if not TRACE_ID_PATTERN.fullmatch(trace_id):
            raise ValueError("trace_id must contain 1-128 letters, digits, underscores, or hyphens")
        if not self.directory.is_dir():
            return []
        with record_lock(self.directory / ".retention"):
            self._prune()
            paths = []
            for path in self.directory.glob(f"{trace_id}*.jsonl*"):
                match = TRACE_FILE_PATTERN.fullmatch(path.name)
                if match is not None and match.group("trace_id") == trace_id:
                    paths.append(path)
            paths.sort(key=lambda path: (path.name.removesuffix(".1"), not path.name.endswith(".1")))
            records = [json.loads(line) for path in paths
                       for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            return sorted(records, key=lambda record: datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00")))
