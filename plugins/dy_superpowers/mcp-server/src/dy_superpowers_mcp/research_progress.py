"""调研子项覆盖率与固定待补问题模板。"""

from __future__ import annotations

import re
from pathlib import Path

from .memory import validate_module
from .research import record_summary


def _guide_items(module: str) -> list[dict[str, str]]:
    if module not in {"expense", "general-ledger"}:
        raise ValueError("当前只支持费用和总账调研")
    guide = Path(__file__).parents[3] / "skills/module-research/references" / f"{module}-research-guide.md"
    items = {}
    for line in guide.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| ") or not line.endswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 5 or cells[0] != module:
            continue
        order, research_id, question = cells[1:4]
        if module == "expense":
            match = re.fullmatch(r"EXP-(\d{3})", order)
            if not match:
                continue
            order = str(int(match.group(1)))
        items[order, research_id] = {"check_order": order, "research_id": research_id,
                                      "question": question, "attachment": cells[4],
                                      "config_item": question.split("：", 1)[0].strip()}
    if not items:
        raise ValueError(f"{module} 调研指南缺少矩阵映射子项")
    return list(items.values())


def calculate_progress(module: str, records: list[dict], *, scope: str | None = None,
                       scope_id: str | None = None) -> dict:
    module = validate_module(module)
    if module not in {"expense", "general-ledger"}:
        raise ValueError("当前只支持费用和总账调研")
    for name, value in (("scope", scope), ("scope_id", scope_id)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{name} 必须是非空文本")
    module_records = [record for record in records if record.get("module") == module]
    if scope is None and scope_id is None:
        scopes = {record.get("scope_id") for record in module_records if record.get("scope_id")}
        if len(scopes) > 1:
            raise ValueError("该模块存在多个适用范围；请指定 scope 或 scope_id，不能跨范围合并")
    selected = [record for record in module_records
                if (scope is None or record.get("scope") == scope)
                and (scope_id is None or record.get("scope_id") == scope_id)]
    if scope_id is None and scope is not None:
        scope_ids = {record.get("scope_id") for record in selected if record.get("scope_id")}
        if len(scope_ids) > 1:
            raise ValueError("该适用范围关联多个稳定范围标识；请指定 scope_id，不能合并计算")
    items = _guide_items(module)
    expected = {(item["check_order"], item["research_id"]) for item in items}
    answered = {(record.get("check_order"), record.get("research_id")) for record in selected
                if record.get("status") == "confirmed"
                and record.get("research_complete") is True}
    answered &= expected
    percentage = len(answered) / len(items) * 100 if items else 0
    missing = [item for item in items if (item["check_order"], item["research_id"]) not in answered]
    return {"module": module, "scope": scope, "scope_id": scope_id,
            "answered": len(answered), "total": len(items), "percentage": percentage,
            "route": "interactive" if percentage >= 70 else "excel", "items": missing,
            "unmapped_records": [record_summary(record) for record in selected
                                 if (record.get("check_order"), record.get("research_id")) not in expected],
            "completeness_unknown": [record_summary(record) for record in selected
                                     if record.get("status") == "confirmed"
                                     and "research_complete" not in record]}


def calculate_routing_progress(module: str, records: list[dict], *, scope: str | None = None,
                               scope_id: str | None = None) -> dict:
    """未选范围也可分流；多范围返回独立进度及不扣减答案的通用清单。"""
    if scope is not None or scope_id is not None:
        result = calculate_progress(module, records, scope=scope, scope_id=scope_id)
        if scope is None:
            result["scope"] = next((record.get("scope") for record in records
                                    if record.get("module") == module and record.get("scope_id") == scope_id), None)
        result["scope_mode"] = "selected"
        return result

    result = calculate_progress(module, [])
    scope_ids = sorted({record["scope_id"] for record in records if record.get("module") == module})
    if len(scope_ids) == 1:
        result = calculate_progress(module, records, scope_id=scope_ids[0])
        result["scope"] = next(record.get("scope") for record in records
                               if record.get("module") == module and record["scope_id"] == scope_ids[0])
        result["scope_mode"] = "selected"
    elif scope_ids:
        result["scope_mode"] = "multiple"
        result["scope_progress"] = []
        for selected_id in scope_ids:
            selected = calculate_progress(module, records, scope_id=selected_id)
            result["scope_progress"].append({
                "scope_id": selected_id,
                "scope": next(record.get("scope") for record in records
                              if record.get("module") == module and record["scope_id"] == selected_id),
                **{key: selected[key] for key in ("answered", "total", "percentage", "route")},
            })
            for field in ("unmapped_records", "completeness_unknown"):
                result[field].extend(selected[field])
    else:
        result["scope_mode"] = "unscoped"
    return result


def export_unanswered_template(workspace: str | Path, module: str, trace_id: str, *,
                               records: list[dict], scope: str | None = None, scope_id: str | None = None,
                               confirmed: bool) -> dict:
    if confirmed is not True:
        raise PermissionError("导出待补问题清单需要用户明确确认")
    if not isinstance(trace_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", trace_id):
        raise ValueError("trace_id 格式不合法")
    progress = calculate_routing_progress(module, records, scope=scope, scope_id=scope_id)
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    output_dir = Path(workspace) / ".dy_superpowers" / "research" / module
    workspace_path = Path(workspace).resolve()
    current = Path(workspace)
    for part in (".dy_superpowers", "research", module):
        current = current / part
        if current.is_symlink():
            raise ValueError("待补问题清单目录不得包含符号链接")
    output_dir.mkdir(parents=True, exist_ok=True)
    if not output_dir.resolve().is_relative_to(workspace_path):
        raise ValueError("待补问题清单必须保存在当前工作区")
    output = output_dir / "unanswered-questions.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "未回答问题"
    headers = ["模块", "检查顺序", "调研子项", "配置项", "待补充问题与提取要素",
               "适用公司/院区", "适用业务范围", "上线阶段/期间", "答案", "依据/来源", "附件/补充材料"]
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2F6B5F")
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for item in progress["items"]:
        sheet.append([module, item["check_order"], item["research_id"], item["config_item"],
                      item["question"], progress["scope"] or progress["scope_id"], "", "", "", "", ""])
    widths = [18, 18, 14, 32, 64, 24, 28, 24, 48, 36, 32]
    for index, width in enumerate(widths, 1):
        sheet.column_dimensions[chr(64 + index)].width = width
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    try:
        with output.open("xb") as stream:
            workbook.save(stream)
    except FileExistsError:
        raise
    except OSError:
        if output.exists() and not output.is_symlink():
            output.unlink()
        raise
    return {"module": module, "path": str(output.relative_to(workspace)),
            "question_count": len(progress["items"]), "scope": progress["scope"],
            "scope_id": progress["scope_id"], "scope_mode": progress["scope_mode"]}
