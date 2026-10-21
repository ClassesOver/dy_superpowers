"""Minimal structured trace storage with redaction and workspace isolation."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SENSITIVE_PARTS = ("password", "passwd", "token", "secret", "api_key", "apikey", "credential")
REDACTED = "[REDACTED]"
TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
EVENT_FIELDS = {
    "event", "span_id", "parent_span_id", "operation", "tool", "status",
    "duration_ms", "error_type", "project_id", "target_id", "plan_version", "source_ref", "memory_file",
    "skill", "check_order", "research_id", "result", "evidence_ref",
    "model", "module", "count",
}
SECRET_VALUE_PATTERN = re.compile(
    r"(?i)((?:password|passwd|token|secret|api[_-]?key|credential)\s*[:=]\s*)([^\s,;]+)|"
    r"(Bearer\s+)([A-Za-z0-9._~+/=-]+)"
)
TRACE_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_.:/#-]{1,256}$")
TRACE_RESULTS = {"通过", "部分通过", "不通过", "不适用", "待确认", "未检查", "未知", "ok", "error"}
OTEL_EVENT_FIELDS = {
    "event", "operation", "tool", "status", "duration_ms", "error_type",
    "skill", "check_order", "result", "memory_file",
    "model", "module", "count", "research_id",
}


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: REDACTED if any(part in key.lower() for part in SENSITIVE_PARTS) else redact(item)
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


def otel_event_attributes(event: dict[str, Any]) -> dict[str, str | int | float | bool]:
    safe_event = sanitize_trace_event(event)
    return {
        f"dy.{key}": value
        for key, value in safe_event.items()
        if key in OTEL_EVENT_FIELDS
    }


class TraceStore:
    def __init__(self, workspace: str | Path, root: str | Path | None = None) -> None:
        self.workspace = Path(workspace).resolve()
        codex_root = Path(root or os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        workspace_id = hashlib.sha256(str(self.workspace).encode()).hexdigest()[:16]
        self.directory = codex_root / "dy_superpowers" / "traces" / workspace_id

    def append(self, trace_id: str, event: dict[str, Any]) -> Path:
        if not TRACE_ID_PATTERN.fullmatch(trace_id):
            raise ValueError("trace_id must contain 1-128 letters, digits, underscores, or hyphens")
        self.directory.mkdir(parents=True, exist_ok=True)
        safe_event = sanitize_trace_event(event)
        record = redact({**safe_event, "trace_id": trace_id, "timestamp": datetime.now(timezone.utc).isoformat()})
        path = self.directory / f"{trace_id}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")
        return path

    def read(self, trace_id: str) -> list[dict[str, Any]]:
        if not TRACE_ID_PATTERN.fullmatch(trace_id):
            raise ValueError("trace_id must contain 1-128 letters, digits, underscores, or hyphens")
        path = self.directory / f"{trace_id}.jsonl"
        if not path.is_file():
            return []
        with path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
