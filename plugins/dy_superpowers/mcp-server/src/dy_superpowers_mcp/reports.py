"""经校验并经确认的工作区巡检报告。"""

from __future__ import annotations

import copy
import json
import os
import re
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .qa_store import publication_lock
from .qa_templates import MODULE_LABELS

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_MARKDOWN_SPECIAL = set(r"\`*_{}[]<>()#+-.!|")


def _text(value: object) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value if value is not None else "").replace("\r", " ").replace("\n", " ").replace("\t", " ")


def _markdown_text(value: object) -> str:
    return "".join("\\" + char if char in _MARKDOWN_SPECIAL else char for char in _text(value))


def _code_fence(value: str) -> str:
    longest = max((len(run) for run in re.findall(r"`+", value)), default=0)
    return "`" * max(3, longest + 1)


def _business_refs(refs: list) -> str:
    labels = []
    for ref in refs:
        kind = ref.get('kind')
        if kind == 'answer':
            label = '已确认调研答案'
        elif kind == 'odoo':
            label = '当前系统只读核验（详见核验方式与原因）'
        else:
            name = Path(ref.get('reference', '')).name
            label = ('人工签认材料：' if kind == 'manual' else '材料：') + name
        if label not in labels:
            labels.append(label)
    return '；'.join(labels) or '尚无核验材料'


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


def _write_new_bytes(path: Path, data: bytes) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    except FileExistsError as exc:
        raise ValueError("报告编号已存在；历史报告不会被覆盖") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_report(workspace: str | Path, module: str, report: dict, validation: dict) -> tuple[str, Path, Path]:
    """保存固定结构交付物：业务 PDF 与含机器可读数据的 Markdown 同目录同名。"""
    if validation.get("valid") is not True:
        raise ValueError("巡检报告校验未通过，不能保存")
    from .qa_store import read_qa
    report = copy.deepcopy(report)
    report['scope'] = read_qa(workspace, module, report['basis']['scope_id'])['scope']
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    report_id = f"{timestamp}-{secrets.token_hex(3)}"
    path = report_path(workspace, module, report_id)
    pdf_path = path.with_suffix(".pdf")
    path.parent.mkdir(parents=True, exist_ok=True)
    content = _render_report(module, report_id, report, validation)
    from .report_pdf import render_report_pdf

    pdf_bytes = render_report_pdf(module, report_id, report, validation,
                                  generated_at=validation.get("validated_at", ""))  # 先渲染，失败时不落盘任何交付物
    from .inspection import validate_inspection
    with publication_lock(workspace, module):
        current = validate_inspection(Path(workspace), report)
        if not current.get('valid') or current.get('basis', {}).get('module') != module:
            raise ValueError('巡检报告依据或版本已变化，最终校验失败，不能保存')
        path = report_path(workspace, module, report_id)
        pdf_path = path.with_suffix('.pdf')
        if path.is_symlink() or pdf_path.is_symlink():
            raise ValueError("巡检报告文件不允许使用符号链接")
        markdown_written = False
        pdf_written = False
        try:
            _write_new_file(path, content)
            markdown_written = True
            _write_new_bytes(pdf_path, pdf_bytes)
            pdf_written = True
        except Exception:
            # 报告是 Markdown/PDF 成对交付物；失败时清理本次调用已创建的文件。
            if markdown_written:
                path.unlink(missing_ok=True)
            if pdf_written:
                pdf_path.unlink(missing_ok=True)
            raise
    return report_id, path, pdf_path


def _read_raw_report(workspace: str | Path, module: str, report_id: str) -> str:
    path = report_path(workspace, module, report_id)
    if path.is_symlink():
        raise ValueError("巡检报告文件不允许使用符号链接")
    if not path.is_file():
        raise ValueError("未找到指定巡检报告")
    return path.read_text(encoding="utf-8")


def read_report(workspace: str | Path, module: str, report_id: str) -> str:
    """默认仅返回业务正文，内部关联数据只供重导和追溯。"""
    content = _read_raw_report(workspace, module, report_id)
    if '\n## 机器可读数据\n' not in content:
        return content
    return content.split('\n## 机器可读数据\n', 1)[0].rstrip() + '\n'


def export_report_pdf(workspace: str | Path, module: str, report_id: str) -> tuple[Path, bool]:
    """从已存 Markdown 报告的机器可读数据重导出业务 PDF；已有 PDF 不覆盖。"""
    import json as _json

    content = _read_raw_report(workspace, module, report_id)
    blocks = re.findall(r"\n(`{3,})json\n(.*?)\n\1", content, re.DOTALL)
    if not blocks:
        raise ValueError("报告缺少机器可读 JSON 数据，无法重导出 PDF")
    try:
        report = _json.loads(blocks[-1][1])
    except _json.JSONDecodeError as exc:
        raise ValueError("报告机器可读数据损坏，无法重导出 PDF") from exc
    if not isinstance(report, dict):
        raise ValueError("报告机器可读数据损坏，无法重导出 PDF")
    basis = report.get('basis')
    if not isinstance(basis, dict) or not isinstance(basis.get('module'), str) or basis['module'] != module:
        raise ValueError("报告数据与模块不匹配，无法重导出 PDF")
    saved_times = re.findall(r"^- 校验时间：([^\r\n]*)\r?$", content, re.MULTILINE)
    if len(saved_times) != 1:
        raise ValueError("报告必须包含唯一的独立校验时间行，无法重导出 PDF")
    saved_time = saved_times[0]
    try:
        parsed_time = datetime.fromisoformat(saved_time.replace("Z", "+00:00"))
        if parsed_time.tzinfo is None:
            raise ValueError("缺少时区")
    except ValueError as exc:
        raise ValueError("报告校验时间必须为带时区的 ISO 8601 时间，无法重导出 PDF") from exc
    from .inspection import validate_inspection
    from .report_pdf import render_report_pdf

    validation = validate_inspection(Path(workspace), report)
    if not validation.get("valid"):
        raise ValueError("报告不符合当前模板 QA 合同（旧格式、版本或材料已变化），不能重导出 PDF")
    validation["validated_at"] = saved_time
    pdf_path = report_path(workspace, module, report_id).with_suffix(".pdf")
    if pdf_path.is_symlink():
        raise ValueError("巡检报告文件不允许使用符号链接")
    if pdf_path.is_file():
        with publication_lock(workspace, module):
            if not validate_inspection(Path(workspace), report).get('valid'):
                raise ValueError('报告依据或版本已变化，最终校验失败，不能重导出 PDF')
            return pdf_path, False
    pdf_bytes = render_report_pdf(module, report_id, report, validation, generated_at=saved_time)
    with publication_lock(workspace, module):
        if not validate_inspection(Path(workspace), report).get('valid'):
            raise ValueError('报告依据或版本已变化，最终校验失败，不能重导出 PDF')
        pdf_path = report_path(workspace, module, report_id).with_suffix('.pdf')
        if pdf_path.is_symlink():
            raise ValueError('巡检报告文件不允许使用符号链接')
        if pdf_path.is_file():
            return pdf_path, False
        _write_new_bytes(pdf_path, pdf_bytes)
    return pdf_path, True


def _render_report(module: str, report_id: str, report: dict, validation: dict) -> str:
    from .qa_templates import load_template
    questions = {q['question_id']: q for q in load_template(module)['questions']}
    lines = [f'# {MODULE_LABELS.get(module, module)}模块巡检报告', '',
        f'- 报告编号：{report_id}', f'- 校验时间：{validation["validated_at"]}',
        f'- 门禁：{validation["gate"]}', f'- 范围：{_markdown_text(report.get("scope", "未提供业务范围"))}',
        f'- 模板覆盖：{len(report["records"])}/{len(questions)} 提取点；未检查项仍保留。', '',
        '## 未验证边界', validation['limitations'], '', '## 模板问题与业务要求']
    group = None
    for record in report['records']:
        q = questions[record['question_id']]
        if q['check_order'] != group:
            group = q['check_order']
            lines += ['', f'### {_markdown_text(q["research_id"])} {_markdown_text(q["config_item"])}']
        lines += ['', f'#### {_markdown_text(q["question"])}',
            f'- 结果：{record["result"]}',
            f'- 核验方式：{_markdown_text(record["verification_method"])}；原因：{_markdown_text(record["reason"])}']
        for requirement in record['requirements']:
            lines += [f'- 业务要求：{_markdown_text(requirement["text"])}；结果：{requirement["result"]}',
                '- 答案/附件依据：' + _markdown_text(_business_refs(requirement['source_refs'])),
                '- 证据：' + _markdown_text(_business_refs(requirement['evidence_refs']))]
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    fence = _code_fence(payload)
    lines += ['', '## 机器可读数据', fence + 'json', payload, fence, '']
    return '\n'.join(lines)
