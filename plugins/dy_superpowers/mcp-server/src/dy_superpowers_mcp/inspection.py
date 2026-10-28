"""校验声明范围内的巡检记录，不代替业务证据真实性判断。"""

from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from pathlib import Path

from .research import read_record
from .trace import TraceStore

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
    excluded = report.get("excluded_checks", [])
    if not isinstance(excluded, list):
        error("excluded_checks", "范围外检查项必须是列表")
        excluded = []
    expected_keys = set()
    for index, item in enumerate(expected):
        current = key(item, f"expected_checks[{index}]")
        if current in expected_keys:
            error(f"expected_checks[{index}]", "预期检查范围重复")
        if current:
            expected_keys.add(current)
    excluded_keys = set()
    scopes = {}
    for index, item in enumerate(expected):
        current = key(item, f"expected_checks[{index}]")
        if current:
            scopes.setdefault((current[0], current[3], current[4]), set()).add((current[1], current[2]))
    for index, item in enumerate(excluded):
        path = f"excluded_checks[{index}]"
        current = key(item, path)
        if current is None:
            continue
        if current in excluded_keys or current in expected_keys:
            error(path, "范围外检查项重复或同时出现在预期范围中")
        excluded_keys.add(current)
        if not isinstance(item.get("reason"), str) or not item["reason"].strip():
            error(f"{path}.reason", "范围外检查项必须说明排除理由")
        scopes.setdefault((current[0], current[3], current[4]), set()).add((current[1], current[2]))
    for scope, declared in scopes.items():
        try:
            _, standards = _matrix(scope[0])
            matrix_keys = set(standards)
            if declared != matrix_keys:
                error("expected_checks", f"模块 {scope[0]} 的声明范围未完整覆盖矩阵；缺项必须作为 excluded_checks 明确说明")
        except (ValueError, OSError):
            error("expected_checks", f"模块 {scope[0]} 的巡检矩阵不可用")
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
        if verified_at is not None and verified_at.astimezone(timezone.utc) > datetime.now(timezone.utc):
            error(f"{path}.verified_at", "验证时间不能晚于当前时间")
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
            if not isinstance(evidence, list) or not evidence:
                error(f"{path}.evidence_refs", "明确结论必须提供最小证据引用")
            else:
                for evidence_index, evidence_ref in enumerate(evidence):
                    evidence_path = f"{path}.evidence_refs[{evidence_index}]"
                    if not isinstance(evidence_ref, dict) or evidence_ref.get("kind") not in {"odoo", "document", "memory", "manual"} or not isinstance(evidence_ref.get("reference"), str) or not evidence_ref["reference"].strip():
                        error(evidence_path, "证据引用必须包含合法 kind 和非空 reference")
                    elif evidence_ref["kind"] == "odoo":
                        trace_id = evidence_ref.get("trace_id")
                        model = evidence_ref.get("model")
                        if not isinstance(trace_id, str) or not isinstance(model, str) or not model.strip():
                            error(evidence_path, "Odoo 证据必须包含 trace_id 和 model")
                        else:
                            events = TraceStore(workspace).read(trace_id)
                            reads = [event for event in events
                                     if event.get("event") == "odoo.read" and event.get("status") == "ok"
                                     and event.get("tool") in {"dy_odoo_record_search", "dy_odoo_record_read",
                                                               "dy_odoo_module_info", "dy_odoo_module_list_installed"}
                                     and event.get("model") == model
                                     and event.get("operation") in {"search_read", "record_read", "module_info", "module_list_installed"}]
                            expected_context = {"module": record["module"], "check_order": record["check_order"],
                                                "research_id": record["research_id"], "scope_id": record["scope_id"],
                                                "revision": record.get("research_revision"),
                                                "environment": record.get("environment")}
                            reads = [event for event in reads
                                     if isinstance(event.get("trace_context"), dict)
                                     and all(event["trace_context"].get(key) == value
                                             for key, value in expected_context.items())]
                            completed = {(event.get("span_id"), event.get("tool")) for event in events
                                         if event.get("event") == "tool.end" and event.get("status") == "ok"}
                            if not any((event.get("span_id"), event.get("tool")) in completed for event in reads):
                                error(evidence_path, "trace_id 中没有对应模型的成功 Odoo 读取事件")
        if result in DEFINITIVE or record.get("research_revision"):
            try:
                research = read_record(workspace, record["module"], record["check_order"], record["research_id"],
                                       scope_id=record["scope_id"], include_history=False)
                if research is None or research["status"] != "confirmed":
                    error(f"{path}.research_revision", "调研缺失或范围已停用，必须重新调研确认")
                elif research.get("research_complete") is not True and record.get("result") in DEFINITIVE:
                    error(f"{path}.research_revision", "调研结论尚未完整，不能支撑明确巡检结果")
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
    coverage, inspected_total, matrix_total = {}, 0, 0
    for scope, declared in scopes.items():
        expected_pairs = {(key[1], key[2]) for key in expected_keys
                          if (key[0], key[3], key[4]) == scope}
        try:
            _, standards = _matrix(scope[0])
        except (ValueError, OSError):
            continue
        coverage["|".join(scope)] = {"inspected": len(expected_pairs), "excluded": len(declared) - len(expected_pairs),
                                     "matrix": len(standards)}
        inspected_total += len(expected_pairs)
        matrix_total += len(standards)
    limitations = "仅校验声明范围的记录结构、版本和引用对应关系，不验证证据真实性，不代表整个项目交付通过。"
    if matrix_total and inspected_total < matrix_total:
        limitations += (f"本轮为部分范围巡检：纳入 {inspected_total}/{matrix_total} 个矩阵子项，"
                        "门禁仅代表纳入项，不代表整模块交付通过；整体验收仍需覆盖全部子项。")
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
            "coverage": coverage, "limitations": limitations}
