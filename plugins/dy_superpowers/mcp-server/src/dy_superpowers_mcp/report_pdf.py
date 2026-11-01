"""面向业务人员的固定结构巡检报告 PDF（与 Markdown 交付物同目录同名）。

使用 WeasyPrint（HTML/CSS + 系统字体）排版；固定结构依次为：
报告信息、未验证边界、问题分组与逐项业务要求。
完整机器可读 JSON 只保留在 Markdown 报告中，不进入 PDF。
"""
from __future__ import annotations

import html

from .reports import _text, _business_refs
from .qa_templates import MODULE_LABELS


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


def render_report_pdf(module: str, report_id: str, report: dict, validation: dict,
                      generated_at: str = "") -> bytes:
    from .qa_templates import load_template
    from weasyprint import HTML
    questions = {q['question_id']: q for q in load_template(module)['questions']}
    parts = [f'<h1>{_esc(MODULE_LABELS.get(module, module))}模块巡检报告</h1>',
        f'<p>报告编号：{_esc(report_id)}；门禁：{_esc(validation["gate"])}</p>',
        f'<p>校验时间：{_esc(generated_at or validation.get("validated_at", ""))}</p>',
        f'<p>模板覆盖：{len(report["records"])}/{len(questions)} 提取点</p>',
        f'<p>范围：{_esc(report.get("scope", "未提供业务范围"))}</p>',
        f'<h2>未验证边界</h2><p>{_esc(validation["limitations"])}</p>']
    group = None
    for record in report['records']:
        q = questions[record['question_id']]
        if q['check_order'] != group:
            group = q['check_order']
            parts.append(f'<h2>{_esc(q["research_id"])} {_esc(q["config_item"])}</h2>')
        parts += [f'<h3>{_esc(q["question"])}</h3>',
            f'<p>结果：{_esc(record["result"])}</p>',
            f'<p>方式：{_esc(record["verification_method"])}；{_esc(record["reason"])}</p>']
        for requirement in record['requirements']:
            parts += [f'<p><b>业务要求：</b>{_esc(requirement["text"])}；结果：{_esc(requirement["result"])}</p>',
                f'<p>答案/附件依据：{_esc(_business_refs(requirement["source_refs"]))}</p>',
                f'<p>证据：{_esc(_business_refs(requirement["evidence_refs"]))}</p>']
    css = _CSS.replace('__FOOTER_LEFT__', html.escape(f'{MODULE_LABELS.get(module, module)}模块巡检报告｜{report_id}'))
    return HTML(string='<html><head><meta charset="utf-8"><style>' + css + '</style></head><body>' + ''.join(parts) + '</body></html>').write_pdf()
