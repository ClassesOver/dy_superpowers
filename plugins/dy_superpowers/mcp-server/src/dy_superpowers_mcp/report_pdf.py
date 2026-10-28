"""面向业务人员的固定结构巡检报告 PDF（与 Markdown 交付物同目录同名）。

使用 WeasyPrint（HTML/CSS + 系统字体）排版；固定结构依次为：
报告信息、一页结论、范围概览、检查结果汇总、阻塞事项与下一步、技术追溯。
完整机器可读 JSON 只保留在 Markdown 报告中，不进入 PDF。
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone

from .reports import _category_index, _check_label, _text

_MODULE_NAMES = {"expense": "费用", "general-ledger": "总账"}
_STATUSES = ("通过", "部分通过", "不通过", "不适用", "待确认", "未检查", "未知")
_RESULT_COLORS = {"通过": "#1a7f37", "部分通过": "#b58105", "不通过": "#c62828",
                  "待确认": "#b45309", "未检查": "#6b7280", "未知": "#6b7280", "不适用": "#6b7280"}

_CSS = """
@page {
  size: A4; margin: 18mm 16mm 20mm 16mm;
  @bottom-left  { content: "__FOOTER_LEFT__";  font-size: 7.5pt; color: #888; }
  @bottom-right { content: "第 " counter(page) " 页，共 " counter(pages) " 页"; font-size: 7.5pt; color: #888; }
}
* { box-sizing: border-box; }
body { font-family: "Noto Sans CJK SC", "Noto Sans SC", "WenQuanYi Zen Hei", sans-serif;
       font-size: 10pt; color: #1f2430; line-height: 1.6; }
h1 { font-size: 19pt; margin: 0 0 4mm; color: #0f2b46;
     border-bottom: 2.5pt solid #0f2b46; padding-bottom: 2.5mm; }
h2 { font-size: 13pt; margin: 6mm 0 2.5mm; color: #0f2b46;
     border-left: 3.5pt solid #2f6db3; padding-left: 2.5mm; }
.meta { width: 100%; border-collapse: collapse; margin-bottom: 2mm; }
.meta td { border: none; border-bottom: .5pt solid #dde3ea; padding: 1.6mm 2mm; font-size: 9pt; }
.meta td.k { width: 24mm; color: #52606d; white-space: nowrap; }
.gate { display: inline-block; padding: 1mm 4mm; border-radius: 2mm; color: #fff;
        font-weight: bold; font-size: 11.5pt; }
ul { margin: 1mm 0 2mm; padding-left: 6mm; } li { margin: .8mm 0; }
table.grid { width: 100%; border-collapse: collapse; margin: 1.5mm 0 3mm; font-size: 8.4pt; }
table.grid th { background: #0f2b46; color: #fff; padding: 1.8mm 2mm; text-align: left; font-weight: 600; }
table.grid td { border: .5pt solid #c9d3dd; padding: 1.6mm 2mm; vertical-align: top; }
table.grid tr:nth-child(even) td { background: #f4f7fa; }
.result { font-weight: 700; white-space: nowrap; }
.note { background: #fdf6ec; border: .5pt solid #e5c07b; border-radius: 1.5mm;
        padding: 2mm 3mm; margin: 2mm 0; font-size: 9pt; color: #7a5a12; }
.cat { font-weight: 700; color: #2f6db3; margin: 3mm 0 1mm; font-size: 10pt; }
.small { font-size: 8.6pt; color: #52606d; }
"""


def _esc(value: object) -> str:
    return html.escape(_text(value))


def _result_span(result: str) -> str:
    color = _RESULT_COLORS.get(result, "#6b7280")
    return f'<span class="result" style="color:{color}">{_esc(result)}</span>'


def render_report_pdf(module: str, report_id: str, report: dict, validation: dict,
                      generated_at: str = "") -> bytes:
    """生成固定结构 PDF 字节；失败时抛出异常且不落盘任何交付物。"""
    module_name = _MODULE_NAMES.get(module, module)
    records = [record for record in report.get("records", []) if isinstance(record, dict)]
    expected = report.get("expected_checks", [])
    excluded = report.get("excluded_checks", [])
    coverage = validation.get("coverage") if isinstance(validation.get("coverage"), dict) else {}
    inspected = sum(item.get("inspected", 0) for item in coverage.values())
    matrix_size = sum(item.get("matrix", 0) for item in coverage.values())
    counts = {status: sum(record.get("result") == status for record in records) for status in _STATUSES}
    gate = validation["gate"]
    gate_bg = {"通过": "#1a7f37", "不通过": "#c62828"}.get(gate, "#b45309")

    parts: list[str] = [f"<h1>{module_name}模块巡检报告</h1>"]
    parts.append('<table class="meta">')
    parts.append(f"<tr><td class='k'>报告编号</td><td>{_esc(report_id)}</td></tr>")
    parts.append(f"<tr><td class='k'>生成时间</td><td>{_esc(generated_at or report.get('_generated_at') or validation.get('validated_at', ''))}</td></tr>")
    parts.append(f"<tr><td class='k'>交付结论</td><td><span class='gate' style='background:{gate_bg}'>{_esc(gate)}</span>"
                 + ("　暂不能作为交付通过依据" if gate == "待确认" else "") + "</td></tr>")
    parts.append("<tr><td class='k'>巡检覆盖</td><td>"
                 + (_esc(f"纳入 {inspected}/{matrix_size} 个矩阵子项") if matrix_size else _esc(f"{len(expected)} 项"))
                 + "（部分范围：门禁仅代表纳入项）</td></tr></table>")

    parts.append("<h2>一、一页结论</h2><ul>")
    parts.append(f"<li>巡检范围：{module_name}模块；<b>{len(expected)}</b> 项纳入检查，<b>{len(excluded)}</b> 项按范围排除。</li>")
    parts.append("<li>检查结果：" + ("；".join(f"{status} <b>{count}</b> 项" for status, count in counts.items() if count) or "暂无结果。") + "</li>")
    summary = report.get("evidence_summary", {}).get("summary") if isinstance(report.get("evidence_summary"), dict) else None
    parts.append(f"<li>当前判断：{_esc(summary if isinstance(summary, str) and summary.strip() else '按下方检查明细及证据判断；未确认事项不视为通过。')}</li></ul>")
    if matrix_size and inspected < matrix_size:
        parts.append(f'<div class="note">本轮为<b>部分范围巡检</b>（纳入 {inspected}/{matrix_size} 个矩阵子项），门禁仅代表纳入项，不代表整模块交付通过。</div>')
    parts.append('<div style="page-break-after: always"></div>')

    parts.append("<h2>二、范围概览</h2><ul>")
    project = report.get("project_context", {}) if isinstance(report.get("project_context"), dict) else {}
    companies = project.get("companies") or project.get("company_scope") or []
    company_text = "、".join(map(str, companies)) if isinstance(companies, list) else str(companies)
    if not company_text:
        company_text = "范围待确认" if project.get("company_scope_confirmed") is False else "未提供"
    envs = sorted({item.get("environment", "") for item in expected if isinstance(item, dict) and item.get("environment")})
    parts.append(f"<li>纳入检查 {len(expected)} 项；按范围排除 {len(excluded)} 项。</li>")
    parts.append(f"<li>项目范围：公司/组织 {_esc(company_text)}；环境 {_esc('、'.join(envs) or '未提供')}。</li>")
    parts.append("<li>验证口径：只读配置快照 + 来源调研对照；业务验收另需岗位、页面与流程样例。</li></ul>")
    excluded_counts: dict[str, int] = {}
    for item in excluded:
        if isinstance(item, dict):
            excluded_counts[item.get("reason", "未说明")] = excluded_counts.get(item.get("reason", "未说明"), 0) + 1
    if excluded_counts:
        parts.append('<table class="grid"><tr><th style="width:80%">排除理由</th><th>项数</th></tr>')
        parts.extend(f"<tr><td>{_esc(reason)}</td><td>{count}</td></tr>" for reason, count in sorted(excluded_counts.items()))
        parts.append("</table>")

    parts.append("<h2>三、检查结果汇总</h2>")
    category_index = _category_index(module)
    grouped: dict[str, list[dict]] = {}
    for record in records:
        metadata = category_index.get(record.get("check_order", ""), {})
        grouped.setdefault(metadata.get("category", "其他检查"), []).append(record)
    if grouped:
        for category, items in grouped.items():
            parts.append(f'<div class="cat">{_esc(category)}</div>')
            parts.append('<table class="grid"><tr><th>检查项</th><th>配置项</th><th>优先级</th><th>结果</th><th>说明/待办</th></tr>')
            for record in items:
                metadata = category_index.get(record.get("check_order", ""), {})
                parts.append("<tr>"
                             + f"<td>{_esc(_check_label(module, record))}</td>"
                             + f"<td>{_esc(metadata.get('item', '—'))}</td><td>{_esc(metadata.get('priority', '—'))}</td>"
                             + f"<td>{_result_span(record.get('result', '未知'))}</td>"
                             + f"<td>{_esc(record.get('reason', '') or '—')}</td></tr>")
            parts.append("</table>")
    else:
        parts.append("<p>本轮无纳入项记录。</p>")

    parts.append("<h2>四、阻塞事项与下一步</h2>")
    blockers: dict[str, list[str]] = {}
    for record in records:
        if record.get("result") in {"不通过", "部分通过", "待确认", "未检查", "未知"}:
            reason = _text(record.get("reason", "")).strip() or "未提供原因"
            blockers.setdefault(reason, []).append(_check_label(module, record))
    if blockers:
        parts.append("<ol>")
        parts.extend(f"<li><b>待处理（影响 {len(ids)} 项）</b>：{_esc(reason)}</li>" for reason, ids in blockers.items())
        parts.append("</ol>")
    else:
        parts.append("<p>当前没有待处理的阻塞事项。</p>")
    steps = report.get("next_steps", [])
    if steps:
        parts.append("<p><b>建议下一步</b></p><ol>" + "".join(f"<li>{_esc(step)}</li>" for step in steps) + "</ol>")

    parts.append("<h2>五、技术追溯</h2><ul>")
    parts.append(f"<li>报告编号：{_esc(report_id)}；校验时间：{_esc(validation.get('validated_at', ''))}。</li>")
    parts.append(f"<li>结构校验：{'通过' if validation.get('valid') else '失败'}；门禁：{_esc(gate)}。</li>")
    parts.append(f"<li>矩阵版本：{_esc(json.dumps(validation.get('matrix_revisions', {}), ensure_ascii=False, sort_keys=True))}</li>")
    parts.append("<li>完整原始检查数据（机器可读 JSON）见同名 Markdown 报告；本 PDF 为业务交付版，不含机器可读数据。</li></ul>")

    document = (f"<!doctype html><html><head><meta charset='utf-8'><style>"
                + _CSS.replace("__FOOTER_LEFT__", html.escape(f"{module_name}模块巡检报告｜{report_id}"))
                + "</style></head><body>" + "".join(parts) + "</body></html>")
    from weasyprint import HTML

    return HTML(string=document).write_pdf()
