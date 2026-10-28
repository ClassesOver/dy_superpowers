"""完整解析档案仅保存到当前工作区的固定隐藏兄弟目录。"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from .memory import validate_module
from .storage import atomic_write, record_lock
from .trace import redact

ROOT = '.dy_superpowers-parsed'
PARSER_VERSION = 'dy-document-extract/v3'
_HASH = re.compile(r'^[a-f0-9]{64}$')
_PRIVATE = re.compile(r'(?<!\d)(?:1[3-9]\d{9}|\d{17}[\dXx])(?!\d)')


def _safe(root: Path, *parts: str) -> Path:
    current = root
    for part in parts:
        candidate = Path(part)
        if candidate.is_absolute() or '..' in candidate.parts:
            raise ValueError('解析路径不得越界')
        for element in candidate.parts:
            current = current / element
            if current.is_symlink() or not current.resolve().is_relative_to(root):
                raise ValueError('解析目录不允许符号链接或越界')
    return current


def _json(path: Path, value) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def _read(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError) as exc:
        raise ValueError('解析索引或清单损坏，须核对后恢复') from exc


def _clean(value, redactions: list, locator=''):
    if isinstance(value, str):
        safe = _PRIVATE.sub('[REDACTED]', redact(value))
        if safe != value:
            redactions.append({'code': 'sensitive_content_redacted', 'locator': locator,
                               'blocking': False})
        return safe
    if isinstance(value, list):
        return [_clean(item, redactions, f'{locator}/{i}') for i, item in enumerate(value)]
    if isinstance(value, dict):
        result = {}
        sensitive = redact(value)
        for key, item in value.items():
            if sensitive.get(key) != item and sensitive.get(key) == '[REDACTED]':
                redactions.append({'code': 'sensitive_content_redacted', 'locator': f'{locator}/{key}',
                                   'blocking': False})
                result[key] = '[REDACTED]'
            else:
                result[key] = _clean(item, redactions, f'{locator}/{key}')
        return result
    return value


def _render(units: list[dict]) -> str:
    lines = ['# 来源全文解析档案', '', '原文、OCR转写及解释按内容单元分别记录；不代表业务确认。', '']
    for unit in units:
        lines += [f"## {unit['id']} · {unit.get('kind', 'text')}", '',
                  '来源位置：' + str(unit.get('locator', '')), '']
        content = str(unit.get('content', ''))
        longest = max((len(s) for s in re.findall(r'`+', content)), default=0)
        fence = '`' * max(3, longest + 1)
        lines += [fence + 'text', content, fence, '']
    return '\n'.join(lines)


def _clean_asset(asset: dict, redactions: list) -> tuple[bytes, bool]:
    """文本资产同样脱敏；二进制保留原件，不宣称已完成自动脱敏。"""
    raw = asset['data']
    suffix = Path(asset['name']).suffix.lower()
    locator = asset.get('locator', asset['name'])
    if suffix == '.xml':
        root = ET.fromstring(raw)
        changed = False
        grouped = set()
        # Word 段落和 Excel 共享字符串可能把凭据拆成多个 run。
        for container in root.iter():
            if container.tag.rsplit('}', 1)[-1] not in {'p', 'si'}:
                continue
            segments = [[]]
            for node in container.iter():
                if node.tag.rsplit('}', 1)[-1] in {'br', 'cr'}:
                    segments.append([])
                elif node.text is not None and not len(node):
                    segments[-1].append(node)
            for nodes in segments:
                original = ''.join(node.text for node in nodes)
                safe = _clean(original, redactions, locator)
                grouped.update(nodes)
                if safe != original and nodes:
                    nodes[0].text = safe
                    for node in nodes[1:]:
                        node.text = ''
                    changed = True
        for node in root.iter():
            safe_attributes = _clean(dict(node.attrib), redactions, locator)
            for key, value in list(node.attrib.items()):
                safe = safe_attributes[key]
                if safe != value:
                    node.set(key, safe)
                    changed = True
            for field in ('text', 'tail'):
                value = getattr(node, field)
                if value is not None and not (field == 'text' and node in grouped):
                    safe = _clean(value, redactions, locator)
                    if safe != value:
                        setattr(node, field, safe)
                        changed = True
        return (ET.tostring(root, encoding='utf-8', xml_declaration=True) if changed else raw), changed
    if suffix in {'.md', '.markdown', '.txt', '.json', '.csv'}:
        encoding = 'utf-8-sig'
        if raw.startswith((b'\xff\xfe\x00\x00', b'\x00\x00\xfe\xff')):
            encoding = 'utf-32'
        elif raw.startswith((b'\xff\xfe', b'\xfe\xff')):
            encoding = 'utf-16'
        original = raw.decode(encoding)
        safe = _clean(original, redactions, locator)
        return (safe.encode('utf-8') if safe != original else raw), safe != original
    return raw, False


def _validated_run(workspace: Path, relative: str, source_sha: str) -> dict:
    path = _safe(workspace, relative)
    expected_parent = workspace / ROOT / source_sha / 'runs'
    if path.parent != expected_parent:
        raise ValueError('解析清单中的轮次路径越界')
    manifest = _read(_safe(workspace, relative, 'manifest.json'), None)
    if not isinstance(manifest, dict) or manifest.get('source_sha256') != source_sha:
        raise ValueError('解析轮次清单无效')
    for name, digest in manifest['files'].items():
        file = _safe(workspace, relative, name)
        if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != digest:
            raise ValueError('解析档案摘要不一致，须显式重新解析并保留原轮次')
    return manifest


def archive_source(workspace: Path, source: Path, module: str, *, force=False,
                   extractor=None, candidates=None) -> dict:
    """按内容指纹归档；失败轮次保留并不替换最近完整轮次。"""
    workspace = Path(workspace).resolve()
    if not workspace.is_dir():
        raise ValueError('工作区必须存在')
    validate_module(module)
    source = Path(source)
    if not source.is_file():
        raise ValueError('来源文件不存在或不是普通文件')
    source_bytes = source.read_bytes()
    sha = hashlib.sha256(source_bytes).hexdigest()
    candidate_bytes = json.dumps(candidates or {}, ensure_ascii=False, sort_keys=True,
                                 separators=(',', ':')).encode('utf-8')
    candidate_sha = hashlib.sha256(candidate_bytes).hexdigest()
    root = _safe(workspace, ROOT)
    root.mkdir(exist_ok=True)
    index_path = _safe(workspace, ROOT, 'index.json')
    options = {'module': module, 'suffix': source.suffix.lower(), 'candidate_sha256': candidate_sha}
    with record_lock(index_path):
        directory = _safe(workspace, ROOT, sha)
        directory.mkdir(exist_ok=True)
        file_manifest_path = _safe(workspace, ROOT, sha, 'manifest.json')
        file_manifest = _read(file_manifest_path, {'source_sha256': sha, 'latest_complete': None})
        previous = file_manifest.get('latest_complete')
        if previous and not force:
            manifest = _validated_run(workspace, previous, sha)
            if manifest['parser_version'] == PARSER_VERSION and manifest['options'] == options:
                return {**manifest, 'path': previous, 'reused': True}
        run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + secrets.token_hex(4)
        relative = f'{ROOT}/{sha}/runs/{run_id}'
        run = _safe(workspace, relative)
        run.mkdir(parents=True)
        _safe(workspace, relative, 'assets').mkdir()
        if extractor is None:
            from .document_extract import extract_document
            extractor = extract_document
        failed = False
        try:
            extracted = extractor(source)
        except Exception as exc:
            failed = True
            extracted = {'units': [], 'assets': [], 'inventory': {},
                         'issues': [{'code': 'document_parse_failed', 'blocking': True,
                                     'error_type': type(exc).__name__}]}
        if hashlib.sha256(source.read_bytes()).hexdigest() != sha:
            failed = True
            extracted['issues'].append({'code': 'source_changed_during_parse', 'blocking': True})
        redactions = []
        units = _clean(extracted['units'], redactions)
        issues = _clean(extracted.get('issues', []), redactions)
        inventory = _clean(extracted.get('inventory', {}), redactions)
        assets = []
        for number, asset in enumerate(extracted.get('assets', []), 1):
            # 内嵌名称不可成为输出路径；由程序编号并限制扩展名。
            extension = Path(asset['name']).suffix.lower()
            extension = extension if re.fullmatch(r'\.[a-z0-9]{1,10}', extension) else '.bin'
            asset_path = _safe(workspace, relative, 'assets', f'{number:06d}{extension}')
            try:
                data, redacted = _clean_asset(asset, redactions)
            except (UnicodeError, ET.ParseError):
                issues.append({'code': 'asset_text_redaction_failed', 'blocking': True,
                               'locator': asset.get('locator', f'asset:{number}'),
                               'message': '文本资产无法安全脱敏，未写出副本；需核对原始来源。'})
                continue
            asset_path.write_bytes(data)
            assets.append(_clean({key: value for key, value in asset.items() if key != 'data'}, redactions)
                          | {'path': asset_path.relative_to(run).as_posix(), 'redacted': redacted})
        clean_candidates = _clean(candidates or {}, redactions)
        candidate_issues = clean_candidates.get('issues', []) if isinstance(clean_candidates, dict) else []
        if isinstance(candidate_issues, list):
            issues.extend({**issue, 'source_component': 'candidate_parser'}
                          for issue in candidate_issues if isinstance(issue, dict))
        issues.extend(redactions)
        unresolved = sum(issue.get('blocking', True) for issue in issues)
        status = 'failed' if failed else 'partial' if unresolved else 'complete'
        coverage = {'units': len(units), 'assets': len(assets), 'unresolved': unresolved,
                    'redactions': len(redactions), 'status': status, 'inventory': inventory}
        payloads = {'content.json': {'units': units, 'assets': assets},
                    'candidates.json': clean_candidates, 'coverage.json': coverage, 'issues.json': issues}
        for name, data in payloads.items():
            _json(_safe(workspace, relative, name), data)
        atomic_write(_safe(workspace, relative, 'content.md'), _render(units))
        files = {file.relative_to(run).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
                 for file in sorted(run.rglob('*')) if file.is_file()}
        safe_name = _clean(source.name, redactions)
        manifest = {'schema': 'dy-document-archive/v1', 'source_sha256': sha,
                    'source_name': safe_name, 'parser_version': PARSER_VERSION, 'options': options,
                    'status': status, 'redactions': len(redactions), 'coverage': coverage, 'files': files,
                    'created_at': datetime.now(timezone.utc).isoformat()}
        _json(_safe(workspace, relative, 'manifest.json'), manifest)
        file_manifest.update({'latest_attempt': relative})
        if status == 'complete':
            file_manifest['latest_complete'] = relative
        _json(file_manifest_path, file_manifest)
        index = _read(index_path, {'schema': 'dy-document-index/v1', 'sources': {}})
        index['sources'][sha] = {'source_name': safe_name, **file_manifest}
        _json(index_path, index)
        return {**manifest, 'path': relative, 'reused': False}
