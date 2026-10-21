"""校验声明范围内的巡检记录，不代替业务证据真实性判断。"""

from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from pathlib import Path

from .research import read_record

KEY_FIELDS = ("module", "check_order", "research_id", "scope_id", "environment")
RESULTS = {"通过", "部分通过", "不通过", "不适用", "待确认", "未检查", "未知"}
DEFINITIVE = {"通过", "部分通过", "不通过", "不适用"}


def _text(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<br\s*/?>", " ", value, flags=re.IGNORECASE)).split())


def _matrix(module: str) -> tuple[str, dict[tuple[str, str], str]]:
    if module not in {"expense", "general-ledger"}:
        raise ValueError("当前只支持费用和总账巡检矩阵")
    path = Path(__file__).parents[3] / "skills" / f"{module}-inspection/references/inspection-matrix.md"
    source = path.read_bytes()
    standards = {}
    check_order = ""
    for line in source.decode("utf-8").splitlines():
        if line.startswith("## 检查顺序 "):
            check_order = line.removeprefix("## 检查顺序 ").strip()
        elif line.startswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            number = re.match(r"^(\d+)｜", cells[0])
            if number and len(cells) >= 2:
                standards[check_order, number.group(1)] = _text(cells[1])
    return hashlib.sha256(source).hexdigest(), standards


def validate_inspection(workspace: Path, report: object) -> dict:
    errors = []
    matrices = {}
    outcomes = []

    def error(path, message):
        errors.append({"path": path, "message": message})

    def key(item, path):
        if not isinstance(item, dict) or any(not isinstance(item.get(field), str) or not item[field].strip() for field in KEY_FIELDS):
            error(path, "必须提供模块、检查顺序、子项、稳定范围标识和环境")
            return None
        return tuple(item[field] for field in KEY_FIELDS)

    if not isinstance(report, dict):
        error("report", "报告必须是对象")
        report = {}
    expected = report.get("expected_checks")
    records = report.get("records")
    if not isinstance(expected, list) or not expected:
        error("expected_checks", "必须声明本次非空的预期检查范围；不能省略未检查项")
        expected = []
    if not isinstance(records, list):
        error("records", "巡检记录必须是列表")
        records = []
    expected_keys = set()
    for index, item in enumerate(expected):
        current = key(item, f"expected_checks[{index}]")
        if current in expected_keys:
            error(f"expected_checks[{index}]", "预期检查范围重复")
        if current:
            expected_keys.add(current)
    seen = set()
    record_ids = set()
    for index, record in enumerate(records):
        path = f"records[{index}]"
        current = key(record, path)
        if current is None:
            continue
        if current in seen or current not in expected_keys:
            error(path, "检查项重复或不属于声明的检查范围")
        seen.add(current)
        for field in ("record_id", "verification_method", "verified_at", "reason", "acceptance_standard", "matrix_revision"):
            if not isinstance(record.get(field), str) or not record[field].strip():
                error(f"{path}.{field}", "必须提供非空文本")
        record_id = record.get("record_id")
        if isinstance(record_id, str):
            if record_id in record_ids:
                error(f"{path}.record_id", "巡检记录标识重复")
            record_ids.add(record_id)
        verified_at = None
        try:
            verified_at = datetime.fromisoformat(record.get("verified_at", "").replace("Z", "+00:00"))
            if verified_at.tzinfo is None:
                error(f"{path}.verified_at", "验证时间必须包含时区")
                verified_at = None
        except (ValueError, AttributeError):
            error(f"{path}.verified_at", "验证时间必须使用 ISO 8601 格式")
        result = record.get("result")
        if not isinstance(result, str) or result not in RESULTS:
            error(f"{path}.result", "巡检结果不属于允许状态")
            continue
        outcomes.append(result)
        try:
            revision, standards = _matrix(record["module"])
            matrices[record["module"]] = revision
            if record.get("matrix_revision") != revision:
                error(f"{path}.matrix_revision", "矩阵版本已变化，请重新核对验收标准")
            actual = standards.get((record["check_order"], record["research_id"]))
            supplied = record.get("acceptance_standard")
            if actual is None or not isinstance(supplied, str) or _text(supplied) != actual:
                error(f"{path}.acceptance_standard", "验收标准必须对应当前矩阵的同一检查子项，保留完整原文")
        except (ValueError, OSError):
            error(f"{path}.module", "模块矩阵不可用")
        if result in DEFINITIVE:
            evidence = record.get("evidence_refs")
            if not isinstance(evidence, list) or not evidence or any(not isinstance(ref, str) or not ref.strip() for ref in evidence):
                error(f"{path}.evidence_refs", "明确结论必须提供最小证据引用")
        if result in DEFINITIVE or record.get("research_revision"):
            try:
                research = read_record(workspace, record["module"], record["check_order"], record["research_id"], scope_id=record["scope_id"])
                if research is None or research["status"] != "confirmed":
                    error(f"{path}.research_revision", "调研缺失或范围已停用，必须重新调研确认")
                elif record.get("research_revision") != research["revision"]:
                    error(f"{path}.research_revision", "调研修订已变化，旧结果须重新验证")
                elif result in DEFINITIVE and verified_at is not None:
                    updated_at = datetime.fromisoformat(research["updated_at"].replace("Z", "+00:00"))
                    if updated_at.tzinfo is None:
                        updated_at = updated_at.replace(tzinfo=timezone.utc)
                    if verified_at < updated_at:
                        error(f"{path}.verified_at", "验证时间早于所用调研修订，请重新验证")
            except ValueError:
                error(f"{path}.research_revision", "对应调研记录损坏或范围不明确")
    if expected_keys - seen:
        error("records", "声明范围中有检查项未记录；应明确标为未检查或待确认")
    gate = "待确认"
    if not errors and outcomes:
        if "不通过" in outcomes:
            gate = "不通过"
        elif all(outcome == "不适用" for outcome in outcomes):
            gate = "不适用"
        elif all(outcome in {"通过", "不适用"} for outcome in outcomes):
            gate = "通过"
    return {"valid": not errors, "gate": gate, "errors": errors, "matrix_revisions": matrices,
            "validated_at": datetime.now(timezone.utc).isoformat(), "execution_authorized": False,
            "limitations": "仅校验声明范围的记录结构、版本和引用对应关系，不验证证据真实性，不代表整个项目交付通过。"}
