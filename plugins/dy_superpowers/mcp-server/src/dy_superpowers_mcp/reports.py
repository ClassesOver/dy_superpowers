"""经校验并经确认的工作区巡检报告。"""

from __future__ import annotations

import json
import html
import os
import re
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_MODULE_NAMES = {"expense": "费用", "general-ledger": "总账"}
_STATUSES = ("通过", "部分通过", "不通过", "不适用", "待确认", "未检查", "未知")
_MARKDOWN_SPECIAL = set(r"\`*_{}[]<>()#+-.!|")


def _text(value: object) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value if value is not None else "").replace("\r", " ").replace("\n", " ").replace("\t", " ")


def _markdown_text(value: object) -> str:
    return "".join("\\" + char if char in _MARKDOWN_SPECIAL else char for char in _text(value))


def _cell(value: object) -> str:
    return _markdown_text(value)


def _code_fence(value: str) -> str:
    longest = max((len(run) for run in re.findall(r"`+", value)), default=0)
    return "`" * max(3, longest + 1)


def _category_index(module: str) -> dict[str, dict[str, str]]:
    matrix = Path(__file__).parents[3] / "skills" / f"{module}-inspection" / "references" / "inspection-matrix.md"
    if not matrix.is_file():
        return {}
    result = {}
    lines = matrix.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        heading = re.match(r"^## 检查顺序 (.+)$", line)
        if not heading:
            continue
        order = heading.group(1).strip()
        for candidate in lines[index + 1:]:
            if candidate.startswith("## "):
                break
            if not candidate.startswith("|") or "---" in candidate:
                continue
            cells = [html.unescape(cell.strip()).replace("<br>", " ") for cell in candidate.strip().strip("|").split("|")]
            if len(cells) >= 4 and cells[0] not in {"配置分类", "配置类别"}:
                result[order] = {"category": cells[0], "item": cells[1], "priority": cells[3]}
                break
    return result


def _render_report(module: str, report_id: str, report: dict, validation: dict) -> str:
    module_name = _MODULE_NAMES.get(module, module)
    records = [record for record in report.get("records", []) if isinstance(record, dict)]
    expected = report.get("expected_checks", [])
    excluded = report.get("excluded_checks", [])
    counts = {status: sum(record.get("result") == status for record in records) for status in _STATUSES}
    blockers = {}
    for record in records:
        if record.get("result") in {"不通过", "部分通过", "待确认", "未检查", "未知"}:
            reason = record.get("reason", "").strip() or "未提供原因"
            blockers.setdefault(reason, []).append(f"{record.get('check_order', '')} + {record.get('research_id', '')}")

    project = report.get("project_context", {})
    if not isinstance(project, dict):
        project = {}
    envs = sorted({item.get("environment", "") for item in expected if isinstance(item, dict) and item.get("environment")})
    companies = project.get("companies") or project.get("company_scope") or []
    company_text = "、".join(map(str, companies)) if isinstance(companies, list) else str(companies)
    if not company_text:
        company_text = "范围待确认" if project.get("company_scope_confirmed") is False else "未提供"
    evidence_summary = report.get("evidence_summary")
    summary = evidence_summary.get("summary") if isinstance(evidence_summary, dict) else None
    if not isinstance(summary, str) or not summary.strip():
        summary = "按下方检查明细及证据判断；未确认事项不视为通过。"
    sections = [
        f"# {module_name}模块巡检报告", "",
        "## 一页结论", "",
        f"**交付结论：{validation['gate']}。** "
        + ("当前仍有检查项待确认或未完成，暂不能作为交付通过依据。" if validation["gate"] == "待确认" else ""), "",
        f"- 巡检范围：{module_name}模块；{len(expected)} 项纳入检查，{len(excluded)} 项按范围排除。",
        f"- 项目范围：公司/组织 {_markdown_text(company_text)}；环境 {_markdown_text('、'.join(envs) or '未提供')}。",
        "- 检查结果：" + "；".join(f"{status} {count} 项" for status, count in counts.items() if count) + ("。" if any(counts.values()) else "暂无结果。"),
        f"- 当前判断：{_markdown_text(summary)}",
        "", "## 阻塞事项与下一步", "",
    ]
    if blockers:
        for index, (reason, check_ids) in enumerate(blockers.items(), 1):
            sections.append(f"{index}. **待处理（影响 {len(check_ids)} 项）**：{_markdown_text(reason)}")
            sections.append("   - 涉及项请见下方按业务分类的检查明细。")
    else:
        sections.append("当前没有待处理的阻塞事项。")
    steps = report.get("next_steps", [])
    if steps:
        sections.extend(["", "**建议下一步**"])
        sections.extend(f"{index}. {_markdown_text(step)}" for index, step in enumerate(steps, 1))
    if blockers:
        sections.extend(["", "责任人和计划完成日期：待项目负责人指定。"])

    category_index = _category_index(module)
    grouped = {}
    for record in records:
        metadata = category_index.get(record.get("check_order", ""), {})
        category = _markdown_text(metadata.get("category", "其他检查"))
        grouped.setdefault(category, []).append((record, metadata))
    sections.extend(["", "## 巡检明细（按业务分类）", ""])
    for category, items in grouped.items():
        sections.extend([f"### {category}", "", "| 检查项 | 配置项 | 优先级 | 结果 | 说明/待办 |", "| --- | --- | --- | --- | --- |"])
        for record, metadata in items:
            label = f"{record.get('check_order', '')} + {record.get('research_id', '')}"
            reason = record.get("reason", "") or "—"
            sections.append("| " + " | ".join(_cell(value) for value in (
                label, metadata.get("item", "—"), metadata.get("priority", "—"), record.get("result", "未知"), reason,
            )) + " |")
        sections.append("")
    sections.extend(["## 检查依据与证据", ""])
    for record in records:
        label = f"{record.get('check_order', '')} + {record.get('research_id', '')}"
        sections.extend([
            f"### {_markdown_text(label)}｜{_markdown_text(record.get('result', '未知'))}", "",
            f"- 验收要求：{_markdown_text(record.get('acceptance_standard', '未提供'))}",
            f"- 验证方式：{_markdown_text(record.get('verification_method', '未提供'))}",
            f"- 说明：{_markdown_text(record.get('reason', '未提供'))}",
            f"- 证据引用：{_markdown_text(json.dumps(record.get('evidence_refs', []), ensure_ascii=False))}", "",
        ])
    machine_data = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    fence = _code_fence(machine_data)
    sections.extend(["## 技术追溯", "", f"- 报告编号：`{report_id}`", f"- 校验时间：{validation['validated_at']}",
                     f"- 矩阵版本：`{json.dumps(validation['matrix_revisions'], ensure_ascii=False, sort_keys=True)}`",
                     f"- 结构校验：{'通过' if validation.get('valid') else '失败'}；门禁：{validation['gate']}。",
                     "- 完整原始检查数据见下方 JSON；用于复核、迁移和自动处理。", "",
                     "## 完整检查数据（机器可读）", "", f"{fence}json",
                     machine_data, fence, ""])
    return "\n".join(sections)


def _write_new_file(path: Path, content: str) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except FileExistsError as exc:
        raise ValueError("报告编号已存在；历史报告不会被覆盖") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def report_path(workspace: str | Path, module: str, report_id: str) -> Path:
    if not _IDENTIFIER.fullmatch(module) or not _IDENTIFIER.fullmatch(report_id):
        raise ValueError("模块或报告编号格式不合法")
    workspace_path = Path(workspace).resolve()
    memory_root = workspace_path / ".dy_superpowers"
    reports_root = memory_root / "reports"
    module_dir = reports_root / module
    match = re.fullmatch(r"(\d{4})(\d{2})(\d{2})T\d{12}Z-[0-9a-f]{6}", report_id)
    report_dir = module_dir / f"{match[1]}-{match[2]}-{match[3]}" if match else module_dir
    if any(path.is_symlink() for path in (memory_root, reports_root, module_dir, report_dir)):
        raise ValueError("巡检报告目录不允许使用符号链接")
    if not module_dir.resolve().is_relative_to(memory_root.resolve()):
        raise ValueError("巡检报告必须保存在当前工作区")
    return report_dir / f"{report_id}.md"


def save_report(workspace: str | Path, module: str, report: dict, validation: dict) -> tuple[str, Path]:
    if validation.get("valid") is not True:
        raise ValueError("巡检报告校验未通过，不能保存")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    report_id = f"{timestamp}-{secrets.token_hex(3)}"
    path = report_path(workspace, module, report_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = _render_report(module, report_id, report, validation)
    if path.is_symlink():
        raise ValueError("巡检报告文件不允许使用符号链接")
    _write_new_file(path, content)
    return report_id, path


def read_report(workspace: str | Path, module: str, report_id: str) -> str:
    path = report_path(workspace, module, report_id)
    if path.is_symlink():
        raise ValueError("巡检报告文件不允许使用符号链接")
    if not path.is_file():
        raise ValueError("未找到指定巡检报告")
    return path.read_text(encoding="utf-8")
