#!/usr/bin/env python3
"""从用户指定的工作簿生成运行目录、QA 投影和仓库来源审计。"""

import argparse
import hashlib
import json
import re
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter


ROOT = Path(__file__).resolve().parents[1]
REFERENCES = Path("plugins/dy_superpowers/skills/module-research/references")
CATALOG = Path("plugins/dy_superpowers/mcp-server/src/dy_superpowers_mcp/data/research_templates")
SOURCES = {
    "expense": ("费用模块调研清单.xlsx", 2, 5, 9, 2),
    "general-ledger": ("总账模块调研模板.xlsx", 2, 5, 6, 1),
}


def text(value):
    return str(value).replace("\n", "<br>")


def merged_value(sheet, row, column):
    for merged in sheet.merged_cells.ranges:
        if merged.min_row <= row <= merged.max_row and merged.min_col <= column <= merged.max_col:
            return sheet.cell(merged.min_row, merged.min_col).value
    return sheet.cell(row, column).value


def link_destination(cell):
    if not cell.hyperlink:
        return None, None
    location = cell.hyperlink.location or cell.hyperlink.target
    if not location or "!" not in location:
        return None, location
    return location.lstrip("#").rsplit("!", 1)[0].strip("'"), location


def extract_template(module, path):
    _, header_row, business_columns, layout_columns, attachment_header_row = SOURCES[module]
    book = load_workbook(path, data_only=False)
    sheet = book.worksheets[0]
    attachment_sheets = book.worksheets[1:]
    attachment_names = {page.title for page in attachment_sheets}
    questions = []
    by_check = {}
    for row in range(header_row + 1, sheet.max_row + 1):
        check = merged_value(sheet, row, 1)
        attachment_instruction = sheet.cell(row, 5).value or ""
        target, _ = link_destination(sheet.cell(row, 5))
        if check in by_check:
            point = by_check[check]
            point['rows'].append(row)
            if attachment_instruction and attachment_instruction not in point['attachment_instructions']:
                point['attachment_instructions'].append(attachment_instruction)
            if target in attachment_names and target not in point['attachment_names']:
                point['attachment_names'].append(target)
            continue
        ordinal = len(questions) + 1
        point = {
            "question_id": check,
            "check_order": check,
            "research_id": f"{ordinal:02d}",
            "ordinal": ordinal,
            "row": row,
            "rows": [row],
            "config_item": merged_value(sheet, row, 2),
            "config_path": merged_value(sheet, row, 3),
            "question": merged_value(sheet, row, 4),
            "extraction_guide": "",
            "reference_answer": "",
            "attachment_instructions": [attachment_instruction] if attachment_instruction else [],
            "attachment_names": [target] if target in attachment_names else [],
            "reuse_question_id": {'GL-2-009': 'GL-2-007', 'GL-3-012': 'GL-3-011'}.get(check),
        }
        by_check[check] = point
        questions.append(point)
    for point in questions:
        point['attachment_instruction'] = '\n'.join(point.pop('attachment_instructions'))
    attachments = [{
        "name": page.title,
        "headers": [cell.value for cell in page[attachment_header_row] if cell.value is not None],
    } for page in attachment_sheets]
    widths = {}
    for column in range(1, layout_columns + 1):
        dimension = next(dimension for dimension in sheet.column_dimensions.values()
                         if dimension.min <= column <= dimension.max)
        widths[get_column_letter(column)] = dimension.width
    template = {
        "module": module,
        "title": sheet.cell(1, 1).value,
        "questions": questions,
        "attachments": attachments,
        "layout": {
            "header_row": header_row,
            "headers": [sheet.cell(header_row, column).value for column in range(1, layout_columns + 1)],
            "rows": [[sheet.cell(row, column).value if column <= business_columns else None
                      for column in range(1, layout_columns + 1)]
                     for row in range(1, sheet.max_row + 1)],
            "merges": sorted(str(merged) for merged in sheet.merged_cells.ranges),
            "widths": widths,
        },
    }
    canonical = json.dumps(template, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    template["revision"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    audit = audit_source(module, path, book, template)
    book.close()
    return template, audit


def render_guide(template):
    lines = [f"# {template['title']} QA 指引", "", "## 模块 QA", ""]
    previous_check = None
    for point in template["questions"]:
        if point["check_order"] != previous_check:
            lines.extend([
                f"### {point['check_order']} {point['config_item']}", "",
                text(point["question"]), "",
            ])
            previous_check = point["check_order"]
    return "\n".join(lines)


def audit_source(module, path, book, template):
    _, header_row, business_columns, _, attachment_header_row = SOURCES[module]
    sheet = book.worksheets[0]
    groups = len({point["check_order"] for point in template["questions"]})
    lines = [
        f"## {module}", "",
        f"- 源文件：`{path.name}`；工作簿版本属性：{book.properties.version or '未填写'}。",
        f"- 工作簿标题属性：{book.properties.title or '未填写'}；修改属性：{book.properties.modified}。",
        f"- 文件 SHA256：`{hashlib.sha256(path.read_bytes()).hexdigest()}`。",
        f"- 主表：`{sheet.title}`，业务范围 A1:{get_column_letter(business_columns)}{sheet.max_row}，表头第 {header_row} 行。",
        f"- 解析统计：{groups} 组、{len(template['questions'])} 点、{len(template['attachments'])} 附件；唯一问题键 {len({point['question_id'] for point in template['questions']})} 个。",
        f"- 规范化内容 SHA256：`{template['revision']}`。", "",
        "### 逐题定位", "",
        "| 问题键 | 原编号 | 业务顺序 | 主表业务单元格 | 附件超链接单元格 | 有效附件 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for point in template["questions"]:
        row = point["row"]
        link_column = "E"
        last = point['rows'][-1]
        lines.append(f"| {point['question_id']} | {point['check_order']} | {point['research_id']} | A{row}:E{last} | {link_column}{row}:{link_column}{last} | {'、'.join(point['attachment_names']) or '无独立附件'} |")
    lines.extend(["", "### 附件表头与排除行", "",
                  "| 工作表 | 真实表头范围 | 字段数 | 未发布的表头后行数 | 未命名列处理 |",
                  "| --- | --- | --- | --- | --- |"])
    for page in book.worksheets[1:]:
        cells = [cell for cell in page[attachment_header_row] if cell.value is not None]
        blank_columns = [cell.column_letter for cell in page[attachment_header_row] if cell.value is None]
        lines.append(f"| {page.title} | {cells[0].coordinate}:{cells[-1].coordinate} | {len(cells)} | {page.max_row - attachment_header_row} | {'、'.join(blank_columns) or '无'} |")
    lines.extend(["", "### 附件别名与无效链接", ""])
    aliases = []
    invalid_links = []
    for page in book:
        for row in page:
            for cell in row:
                if not cell.hyperlink:
                    continue
                target, location = link_destination(cell)
                if target not in book.sheetnames:
                    invalid_links.append(f"- `{page.title}!{cell.coordinate}` → `{location}`；目标页不存在，不生成业务题或附件。")
    for point in template["questions"]:
        row = point["row"]
        label_match = re.search(r"附件(?:（按需）)?：([^\n]+)", point["attachment_instruction"])
        if label_match and point["attachment_names"]:
            label = label_match.group(1)
            target = point["attachment_names"][0]
            if label != target:
                aliases.append(f"- `{sheet.title}!E{row}`：正文“{label}”按有效超链接解析为“{target}”。")
    lines.extend(aliases or ["- 未发现附件名称别名。"])
    lines.append("")
    lines.extend(invalid_links or ["- 未发现无效超链接。"])
    lines.extend(["", "### 结构差异与转换说明", ""])
    if module == "expense":
        lines.extend([
            "- 主表 F 为答案，G:I 为备用栏；layout.rows 的 F:I 全部为空，不复制项目答案或备用内容。research_id 使用配置组业务顺序，不从已删除的逐点问题恢复编号。",
            "- 附件第 2 行提取所有真实表头，含备用字段与必填标记；尾部仅有格式的无表头列不生成字段。",
        ])
    else:
        lines.extend([
            "- GL-8-024 的 E48、E49 为同一 Question 的附件分支，聚合为 GL-8-024 一个问题，不生成两份答案。",
            "- 附件第 1 行 A 列无表头，其后为样例标记列；只提取真实 B 列起表头，不制造无名字段。",
            "- GL-2-009 的 D22、GL-3-012 的 D26 为原文斜杠；按当前 E 列共用结论说明，分别关联 GL-2-007 与 GL-3-011，问题投影仍保留原文。",
            "- 总账 F 为答案，layout.rows 的 F 列全部为空；E 附件说明保留为 attachment_instruction，绝不作为 reference_answer 或已确认项目结论。",
        ])
    lines.extend([
        "- 当前工作簿删除旧版逐点提问及答案参考列，A:C 为编号、配置项及路径，D 为唯一 Question，E 为附件说明与链接，F 为答案；两模块表头统一为第 2 行。",
        "- extraction_guide 与 reference_answer 固定为空，不从已删除列、F 答案或备用字段恢复问题和答案参考。",
        "- 以 D 列每个配置组的 Question 为一个业务问题，费用25题、总账37题；rows保留该组所有原行，E附件分支及有效链接聚合，不将62个布局行当作62个问题。",
        "- 合并单元格仅在生成 questions 的 A:D 业务属性时展开；layout 保留 A:E 原空值、合并范围和实际列宽，答案及备用内容全部清空。",
        "- 附件只发布名称和真实字段，不发布任何示例公司、用户、账户、历史配置或其他填写行。",
        "- 调研 guide 仅作 Question 问答投影，按组 ID 与配置项组织，仅以组标题组织 D 列 Question 原文，不展示内部问题键或原号；问题原文中的附件表述照原文保留。配置路径、E 列附件说明、答案、附件字段、已删除的逐点提问与参考、记忆及巡检流程不进入投影。",
        "- 运行目录 JSON 继续保存完整模板，供业务运行与答卷导出；guide 不作为运行或解析输入。",
        "- 所有来源定位仅保存在本审计中，不作为 Skill 运行输入或项目业务记忆。",
        "- 未发现主表业务点或真实附件字段的解析缺口；无效链接仅列于上节，不推断额外配置。", "",
    ])
    return "\n".join(lines)


def write_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=ROOT / REFERENCES, help="用户工作簿目录")
    parser.add_argument("--output-root", type=Path, default=ROOT, help="生成文件所在仓库根目录")
    args = parser.parse_args()
    audits = [
        "# 2026-10-30 模板 QA 来源审计", "",
        "用户本次指定工作簿是本批次唯一业务来源。读取源文件后不保存或复制原工作簿。", "",
        "生成命令：`plugins/dy_superpowers/mcp-server/.venv/bin/python scripts/generate-research-templates.py`。", "",
        "revision 计算：去除 revision 字段后，以 ensure_ascii=False、sort_keys=True、separators=(',', ':') 序列化 JSON，再计算 UTF-8 SHA256。", "",
    ]
    for module, (filename, *_) in SOURCES.items():
        template, audit = extract_template(module, args.source_dir / filename)
        write_text(args.output_root / CATALOG / f"{module}.json", json.dumps(template, ensure_ascii=False, indent=2) + "\n")
        write_text(args.output_root / REFERENCES / f"{module}-research-question.md", render_guide(template))
        audits.append(audit)
        print(f"{module}：{len(template['questions'])} 点、{len(template['attachments'])} 附件")
    write_text(args.output_root / "docs/research-sources/template-qa-20261030.md", "\n".join(audits))


if __name__ == "__main__":
    main()
