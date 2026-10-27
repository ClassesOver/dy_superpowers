"""只读的完整文档提取；结构化内容与无法自动覆盖的对象均返回给归档层。"""
from __future__ import annotations

import io
import json
import mimetypes
from pathlib import Path
import posixpath
import shutil
import subprocess
import warnings
import xml.etree.ElementTree as ET
import zipfile


_W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
_A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
_R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def _local(tag: str) -> str:
    return tag.rsplit('}', 1)[-1]


def _unit(result: dict, kind: str, locator: str, content: str, **metadata) -> None:
    result['units'].append({
        'id': f"unit-{len(result['units']) + 1:06d}",
        'kind': kind, 'locator': locator, 'content': content, **metadata,
    })


def _issue(result: dict, code: str, locator: str, message: str, **metadata) -> None:
    result['issues'].append({
        'code': code, 'locator': locator, 'message': message,
        'blocking': True, **metadata,
    })


def _asset(result: dict, name: str, data: bytes, locator: str, **metadata) -> dict:
    asset = {
        'name': name, 'data': data, 'locator': locator,
        'media_type': mimetypes.guess_type(name)[0] or 'application/octet-stream',
        **metadata,
    }
    result['assets'].append(asset)
    return asset


def _ocr_image(result: dict, asset: dict) -> None:
    """仅通过标准输入调用本地 OCR；图片语义仍须视觉复核。"""
    locator = asset['locator']
    _issue(result, 'visual_review_required', locator,
           '已保留图像资产；文字识别不能覆盖图表、布局和其他视觉语义。',
           asset_name=asset['name'])
    binary = shutil.which('tesseract')
    if not binary:
        _issue(result, 'ocr_unavailable', locator, '本地未安装 tesseract，图像尚未 OCR。', asset_name=asset['name'])
        return
    try:
        languages = subprocess.run([binary, '--list-langs'], capture_output=True, timeout=15, check=True)
        available = languages.stdout.decode('utf-8', errors='replace').splitlines()
        if 'chi_sim' not in available:
            _issue(result, 'ocr_language_missing', locator, '缺少 chi_sim 中文识别语言包，图像尚未完成中文 OCR。', asset_name=asset['name'])
            return
        from PIL import Image
        with Image.open(io.BytesIO(asset['data'])) as image:
            frames = getattr(image, 'n_frames', 1)
            for frame in range(frames):
                image.seek(frame)
                stream = io.BytesIO()
                image.convert('RGB').save(stream, format='PNG')
                completed = subprocess.run(
                    [binary, 'stdin', 'stdout', '-l', 'chi_sim+eng' if 'eng' in available else 'chi_sim'],
                    input=stream.getvalue(), capture_output=True, timeout=120, check=True,
                )
                text = completed.stdout.decode('utf-8')
                frame_locator = f'{locator}/frame:{frame + 1}'
                _unit(result, 'ocr_text', frame_locator, text, asset_name=asset['name'])
                if not text.strip():
                    _issue(result, 'ocr_no_text', frame_locator, 'OCR 未识别出文字，不能据此认定图像没有内容。', asset_name=asset['name'])
        asset['ocr_attempted'] = True
    except Exception as exc:
        _issue(result, 'ocr_failed', locator, f'图像 OCR 失败：{exc}', asset_name=asset['name'])


def _xml_content(element: ET.Element) -> str:
    parts = []
    for node in element.iter():
        tag = _local(node.tag)
        if tag in {'t', 'delText', 'instrText', 'v', 'f'} and node.text is not None:
            parts.append(node.text)
        elif tag == 'tab':
            parts.append('\t')
        elif tag in {'br', 'cr'}:
            parts.append('\n')
    return ''.join(parts)


def _relationships(package: zipfile.ZipFile) -> dict[str, list[dict]]:
    result = {}
    for name in package.namelist():
        if not name.endswith('.rels'):
            continue
        root = ET.fromstring(package.read(name))
        parent = posixpath.dirname(posixpath.dirname(name))
        owner = posixpath.join(parent, posixpath.basename(name)[:-5])
        if name == '_rels/.rels':
            owner = ''
        values = []
        for relation in root:
            target = relation.get('Target', '')
            external = relation.get('TargetMode') == 'External'
            values.append({
                'id': relation.get('Id'), 'type': relation.get('Type', '').rsplit('/', 1)[-1],
                'target': target if external else posixpath.normpath(posixpath.join(parent, target)),
                'external': external,
            })
        result[owner] = values
    return result


def _package_objects(path: Path, result: dict, prefix: str) -> None:
    """清点全部包部件，保留图片、图表与嵌入对象，不访问外部关系。"""
    with zipfile.ZipFile(path) as package:
        names = package.namelist()
        relationships = _relationships(package)
        result['inventory']['package_parts'] = names
        result['inventory']['relationships'] = relationships
        for owner, relations in relationships.items():
            for relation in relations:
                if relation['external']:
                    _unit(result, 'external_reference', f'{owner}/relationship:{relation["id"]}',
                          relation['target'], relation_type=relation['type'])
                    if relation['type'] != 'hyperlink':
                        _issue(result, 'external_content_unread', owner,
                               '外部链接内容未访问，需要补充原始对象。', target=relation['target'])
        for name in names:
            locator = f'part:{name}'
            if name.startswith(f'{prefix}/media/'):
                asset = _asset(result, name, package.read(name), locator)
                if Path(name).suffix.lower() in {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tif', '.tiff', '.webp'}:
                    _ocr_image(result, asset)
                else:
                    _issue(result, 'media_visual_review_required', locator,
                           '媒体资产已保留，但无法作为标准位图 OCR；需用对应媒体查看器复核。',
                           asset_name=name)
            elif name.startswith(f'{prefix}/embeddings/') or 'vbaProject' in name:
                _asset(result, name, package.read(name), locator)
                code = 'macro_not_executed' if 'vbaProject' in name else 'embedded_object_unparsed'
                _issue(result, code, locator, '已保留嵌入对象；未执行宏或嵌入程序，需单独解析其内容。', asset_name=name)
            elif name.startswith(f'{prefix}/charts/') and name.endswith('.xml') and '/_rels/' not in name:
                raw = package.read(name)
                root = ET.fromstring(raw)
                values = []
                for node in root.iter():
                    if node.text is not None and _local(node.tag) in {'t', 'v', 'f'}:
                        values.append({'tag': _local(node.tag), 'value': node.text})
                _unit(result, 'chart', locator, '\n'.join(value['value'] for value in values), values=values)
                _asset(result, name, raw, locator)
                if prefix == 'xl':
                    result['inventory']['charts'] = result['inventory'].get('charts', 0) + 1
                _issue(result, 'chart_visual_review_required', locator,
                       '图表全部文本、公式与缓存数据已提取；图形编码和布局需要视觉复核。', asset_name=name)
            elif name.startswith(f'{prefix}/diagrams/') and name.endswith('.xml'):
                raw = package.read(name)
                _asset(result, name, raw, locator)
                root = ET.fromstring(raw)
                _unit(result, 'diagram_text', locator, _xml_content(root))
                _issue(result, 'diagram_visual_review_required', locator, '已保留 SmartArt/图示部件，其图形语义需要视觉复核。', asset_name=name)
        # 原始 XML 保留全部属性与关系，可复核图片锚点、隐藏内容及未解释的样式。
        for name in names:
            if name.startswith(prefix + '/') and name.endswith('.xml') and '/_rels/' not in name:
                if not any(asset['name'] == name for asset in result['assets']):
                    _asset(result, name, package.read(name), f'part:{name}', role='source_xml')


def _text(path: Path, result: dict) -> None:
    raw = path.read_bytes()
    encoding = 'utf-8-sig'
    if raw.startswith((b'\xff\xfe\x00\x00', b'\x00\x00\xfe\xff')):
        encoding = 'utf-32'
    elif raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        encoding = 'utf-16'
    try:
        content = raw.decode(encoding)
    except UnicodeDecodeError:
        # 不使用 replacement 静默丢字符；无法确定编码时保留原始文件供补充。
        _asset(result, path.name, raw, 'file')
        _issue(result, 'text_encoding_unknown', 'file', '文本不是可确定的 Unicode 编码，需要提供编码后完整读取。')
        return
    _unit(result, 'text', f'lines:1-{len(content.splitlines()) or 1}', content, encoding=encoding)
    result['inventory']['lines'] = len(content.splitlines())
    if path.suffix.lower() == '.json':
        try:
            json.loads(content)
        except ValueError as exc:
            _issue(result, 'invalid_json', 'file', f'原文已完整保留，但 JSON 结构无效：{exc}')


def _xlsx(path: Path, result: dict) -> None:
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter('always')
        book = load_workbook(path, data_only=False, keep_links=True)
        cached = load_workbook(path, data_only=True, keep_links=True)
    for warning in captured:
        _issue(result, 'parser_warning', 'workbook', f'Excel 解析器报告可能未覆盖内容：{warning.message}')
    try:
        result['inventory']['sheets'] = len(book.worksheets)
        result['inventory']['sheet_names'] = book.sheetnames
        for sheet in book.worksheets:
            locator = f'sheet:{sheet.title}'
            _unit(result, 'sheet', locator, sheet.title, sheet_state=sheet.sheet_state,
                  max_row=sheet.max_row, max_column=sheet.max_column)
            for merged in sheet.merged_cells.ranges:
                _unit(result, 'merged_range', f'{locator}/range:{merged}', str(merged))
            for (row, column), cell in sorted(sheet._cells.items()):
                if cell.value is None and not cell.comment and not cell.hyperlink:
                    continue
                cell_locator = f'{locator}/cell:{cell.coordinate}'
                column_hidden = any(
                    dimension.hidden and (dimension.min or 0) <= column <= (dimension.max or 0)
                    for dimension in sheet.column_dimensions.values()
                ) or bool(sheet.column_dimensions.get(get_column_letter(column)) and sheet.column_dimensions[get_column_letter(column)].hidden)
                metadata = {
                    'sheet': sheet.title, 'sheet_state': sheet.sheet_state,
                    'coordinate': cell.coordinate, 'row_hidden': bool(sheet.row_dimensions[row].hidden),
                    'column_hidden': column_hidden, 'data_type': cell.data_type,
                }
                if cell.value is not None:
                    value = cell.value
                    if cell.data_type == 'f':
                        if not isinstance(value, str):
                            value = getattr(value, 'text', str(value))
                        metadata['formula'] = value
                        cache = cached[sheet.title][cell.coordinate].value
                        metadata['cached_value'] = cache.isoformat() if hasattr(cache, 'isoformat') else cache
                        if cache is None:
                            _issue(result, 'formula_cache_missing', cell_locator, '公式没有可用缓存值；未执行或重算公式。')
                    _unit(result, 'cell', cell_locator, str(value), **metadata)
                if cell.comment:
                    _unit(result, 'comment', cell_locator + '/comment', cell.comment.text, author=cell.comment.author)
                if cell.hyperlink:
                    _unit(result, 'hyperlink', cell_locator + '/hyperlink',
                          cell.hyperlink.target or cell.hyperlink.location or '',
                          display=cell.hyperlink.display, tooltip=cell.hyperlink.tooltip)
            for index, header in enumerate((sheet.oddHeader, sheet.evenHeader, sheet.firstHeader,
                                            sheet.oddFooter, sheet.evenFooter, sheet.firstFooter), 1):
                for position in ('left', 'center', 'right'):
                    item = getattr(header, position)
                    if item.text is not None:
                        _unit(result, 'header_footer', f'{locator}/header-footer:{index}/{position}', item.text)
            for table in sheet.tables.values():
                _unit(result, 'table_definition', f'{locator}/table:{table.name}', table.ref,
                      name=table.name, columns=[column.name for column in table.tableColumns])
        for name, definition in book.defined_names.items():
            _unit(result, 'defined_name', f'name:{name}', definition.attr_text or '', name=name)
    finally:
        book.close()
        cached.close()
    _package_objects(path, result, 'xl')
    with zipfile.ZipFile(path) as package:
        for name in package.namelist():
            if name.startswith('xl/drawings/') and name.endswith('.xml') and '/_rels/' not in name:
                root = ET.fromstring(package.read(name))
                if any(_local(node.tag) in {'sp', 'cxnSp', 'graphicFrame'} for node in root.iter()):
                    _unit(result, 'drawing_text', f'part:{name}', _xml_content(root))
                    _issue(result, 'drawing_visual_review_required', f'part:{name}', '绘图文字和源 XML 已保留，绘图语义需要视觉复核。')
            if name.startswith('xl/') and any(marker in name for marker in (
                'threadedComments/', 'persons/', 'pivotTables/', 'pivotCache/', 'externalLinks/',
                'queryTables/', 'slicer', 'activeX/', 'ctrlProps/', 'connections.xml',
            )):
                _issue(result, 'unsupported_spreadsheet_part', f'part:{name}', '此 Excel 扩展部件已保留或清点，尚未完整解释。')


def _word_paragraph_text(paragraph: ET.Element) -> str:
    """嵌套文本框段落单独输出，避免把子段落重复拼到外层。"""
    parts = []
    def visit(node):
        for child in node:
            if child.tag == f'{{{_W}}}p':
                continue
            tag = _local(child.tag)
            if tag in {'t', 'delText', 'instrText'} and child.text is not None:
                parts.append(child.text)
            elif tag == 'tab':
                parts.append('\t')
            elif tag in {'br', 'cr'}:
                parts.append('\n')
            elif tag == 'sym':
                parts.append(f"[符号:{child.get(f'{{{_W}}}font', '')}:{child.get(f'{{{_W}}}char', '')}]")
            visit(child)
    visit(paragraph)
    return ''.join(parts)


def _docx(path: Path, result: dict) -> None:
    from docx import Document
    # 使用成熟库验证主文档包；XML 遍历补足库高层 API 未暴露的脚注/修订/文本框。
    Document(path)
    tables = 0
    paragraphs = 0
    with zipfile.ZipFile(path) as package:
        for name in package.namelist():
            if not name.startswith('word/') or not name.endswith('.xml') or '/_rels/' in name:
                continue
            root = ET.fromstring(package.read(name))
            parents = {child: parent for parent in root.iter() for child in parent}
            for index, paragraph in enumerate(root.iter(f'{{{_W}}}p'), 1):
                paragraphs += 1
                metadata = {'part': name}
                ancestor = parents.get(paragraph)
                while ancestor is not None:
                    tag = _local(ancestor.tag)
                    if tag in {'ins', 'del', 'moveFrom', 'moveTo'}:
                        metadata['revision'] = {'del': 'deleted', 'ins': 'inserted'}.get(tag, tag)
                    if tag in {'footnote', 'endnote', 'comment'}:
                        metadata[tag + '_id'] = ancestor.get(f'{{{_W}}}id')
                    if tag == 'txbxContent':
                        metadata['textbox'] = True
                    ancestor = parents.get(ancestor)
                if paragraph.find(f'.//{{{_W}}}delText') is not None:
                    metadata.setdefault('revision', 'deleted')
                _unit(result, 'paragraph', f'part:{name}/paragraph:{index}', _word_paragraph_text(paragraph), **metadata)
            for index, table in enumerate(root.iter(f'{{{_W}}}tbl'), 1):
                tables += 1
                rows = []
                for row in table.findall(f'{{{_W}}}tr'):
                    rows.append(['\n'.join(_word_paragraph_text(p) for p in cell.iter(f'{{{_W}}}p'))
                                 for cell in row.findall(f'{{{_W}}}tc')])
                _unit(result, 'table', f'part:{name}/table:{index}',
                      '\n'.join('\t'.join(row) for row in rows), rows=rows)
            for node in root.iter():
                tag = _local(node.tag)
                if tag in {'altChunk', 'object', 'control', 'contentPart', 'pict'}:
                    _issue(result, 'unsupported_word_object', f'part:{name}/{tag}',
                           '已保留原始 XML 与关联资产，此 Word 对象的完整语义需单独解析。')
                elif tag in {'oMath', 'oMathPara'}:
                    _unit(result, 'equation', f'part:{name}/equation', _xml_content(node))
                    _issue(result, 'equation_visual_review_required', f'part:{name}/equation', '公式文本已保留，二维公式布局需要复核。')
                elif tag == 'hyperlink' and node.get(f'{{{_W}}}anchor'):
                    _unit(result, 'hyperlink', f'part:{name}/anchor', node.get(f'{{{_W}}}anchor', ''))
            if name.startswith('word/customXml/'):
                _issue(result, 'custom_xml_unparsed', f'part:{name}',
                       'Word 自定义 XML 部件已保留，但业务结构尚未解释。')
    result['inventory'].update(tables=tables, paragraphs=paragraphs)
    _package_objects(path, result, 'word')


def _pdf(path: Path, result: dict) -> None:
    import pdfplumber
    from pypdf import PdfReader
    reader = PdfReader(path)
    if reader.is_encrypted:
        _issue(result, 'encrypted_document', 'file', 'PDF 已加密，需要提供解密后的文档。')
        return
    result['inventory']['pages'] = len(reader.pages)
    with pdfplumber.open(path) as document:
        for page_number, (page, layout) in enumerate(zip(reader.pages, document.pages), 1):
            locator = f'page:{page_number}'
            text = page.extract_text() or ''
            _unit(result, 'pdf_text', locator, text)
            layout_text = layout.extract_text(layout=True) or ''
            _unit(result, 'pdf_layout_text', locator + '/layout', layout_text)
            try:
                for index, table in enumerate(layout.extract_tables(), 1):
                    rows = [[str(cell) if cell is not None else '' for cell in row] for row in table]
                    _unit(result, 'table', f'{locator}/table:{index}', '\n'.join('\t'.join(row) for row in rows), rows=rows)
                    result['inventory']['tables'] = result['inventory'].get('tables', 0) + 1
            except Exception as exc:
                _issue(result, 'pdf_table_extraction_failed', locator, f'页面表格提取失败：{exc}')
            image_count = 0
            try:
                for index, item in enumerate(page.images, 1):
                    image_count += 1
                    # pdfplumber exposes image bounds but not decoded image bytes;
                    # pypdf resolves the original embedded image streams.
                    image_data = reader.pages[page_number - 1].images[index - 1].data
                    name = reader.pages[page_number - 1].images[index - 1].name
                    asset = _asset(result, f'page-{page_number}-image-{index}-{name}', image_data,
                                   f'{locator}/image:{index}')
                    _ocr_image(result, asset)
            except Exception as exc:
                _issue(result, 'pdf_image_extraction_failed', locator, f'页面图像提取不完整：{exc}')
            if layout.images and not image_count:
                _issue(result, 'pdf_image_extraction_failed', locator, '页面包含图像，但没有成功提取其独立资产。')
            # 整页图保留图像位置和文本/图像组合，避免把混合页当作纯文本已覆盖。
            if layout.images or not text.strip() or layout.curves or layout.lines or layout.rects:
                _issue(result, 'pdf_page_visual_review_required', locator,
                       '页面含图像、矢量图形或没有可提取文本，需核对整页视觉内容。')
                try:
                    image = layout.to_image(resolution=144).original
                    output = io.BytesIO()
                    image.save(output, format='PNG')
                    asset = _asset(result, f'page-{page_number}.png', output.getvalue(), locator + '/render', role='page_render')
                    _ocr_image(result, asset)
                except Exception as exc:
                    _issue(result, 'pdf_page_render_failed', locator, f'整页渲染失败，需使用视觉工具补齐：{exc}')
            for index, annotation in enumerate(page.get('/Annots', []), 1):
                node = annotation.get_object()
                details = {str(key): str(value) for key, value in node.items() if key not in {'/P', '/AP'}}
                _unit(result, 'annotation', f'{locator}/annotation:{index}', json.dumps(details, ensure_ascii=False), values=details)
                if node.get('/Subtype') not in {'/Link', '/Text', '/FreeText'}:
                    _issue(result, 'pdf_annotation_visual_review_required', f'{locator}/annotation:{index}', '此 PDF 批注的视觉外观需要复核。')
        for name, field in (reader.get_fields() or {}).items():
            _unit(result, 'form_field', f'field:{name}', str(field.get('/V', '')), name=name)
        try:
            for name, values in reader.attachments.items():
                for index, data in enumerate(values, 1):
                    asset_name = f'attachments/{index}-{name}'
                    _asset(result, asset_name, data, f'attachment:{name}/{index}')
                    _issue(result, 'embedded_object_unparsed', f'attachment:{name}/{index}', 'PDF 附件已保留，需要单独解析。', asset_name=asset_name)
        except Exception as exc:
            _issue(result, 'pdf_attachment_extraction_failed', 'file', f'PDF 附件提取失败：{exc}')


def _pptx(path: Path, result: dict) -> None:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    presentation = Presentation(path)
    result['inventory']['slides'] = len(presentation.slides)
    for slide_number, slide in enumerate(presentation.slides, 1):
        hidden = slide._element.get('show', '1') == '0'
        slide_locator = f'slide:{slide_number}'
        _unit(result, 'slide', slide_locator, f'幻灯片 {slide_number}', hidden=hidden)
        def visit(shapes, parent_locator):
            for index, shape in enumerate(shapes, 1):
                locator = f'{parent_locator}/shape:{index}'
                metadata = {'hidden': hidden, 'shape_name': shape.name, 'shape_id': shape.shape_id,
                            'left': shape.left, 'top': shape.top, 'width': shape.width, 'height': shape.height}
                if shape.has_text_frame:
                    for paragraph_number, paragraph in enumerate(shape.text_frame.paragraphs, 1):
                        _unit(result, 'paragraph', f'{locator}/paragraph:{paragraph_number}', paragraph.text, **metadata)
                if shape.has_table:
                    rows = [[cell.text for cell in row.cells] for row in shape.table.rows]
                    _unit(result, 'table', locator + '/table', '\n'.join('\t'.join(row) for row in rows), rows=rows, **metadata)
                    result['inventory']['tables'] = result['inventory'].get('tables', 0) + 1
                if shape.has_chart:
                    chart = shape.chart
                    values = []
                    categories = []
                    try:
                        categories = [str(category.label) for category in chart.plots[0].categories]
                    except (AttributeError, IndexError, TypeError):
                        _issue(result, 'chart_categories_unavailable', locator + '/chart',
                               '图表类别缓存无法读取；原始图表部件已保留。')
                    for series in chart.series:
                        values.append({'name': series.name, 'categories': categories,
                                       'values': list(series.values)})
                    _unit(result, 'chart', locator + '/chart', json.dumps(values, ensure_ascii=False),
                          chart_type=str(chart.chart_type), series=values, **metadata)
                    result['inventory']['charts'] = result['inventory'].get('charts', 0) + 1
                    _issue(result, 'chart_visual_review_required', locator + '/chart',
                           '图表系列数据已提取，图形编码、标题与布局需要视觉复核。')
                if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                    visit(shape.shapes, locator)
                elif shape.shape_type in {MSO_SHAPE_TYPE.EMBEDDED_OLE_OBJECT, MSO_SHAPE_TYPE.LINKED_OLE_OBJECT, MSO_SHAPE_TYPE.MEDIA}:
                    _issue(result, 'unsupported_slide_object', locator, '幻灯片嵌入/链接/影音对象需要单独解析。')
                elif shape.shape_type in {MSO_SHAPE_TYPE.AUTO_SHAPE, MSO_SHAPE_TYPE.FREEFORM, MSO_SHAPE_TYPE.LINE}:
                    _issue(result, 'slide_visual_review_required', locator, '形状文字已提取，连接、图形和空间布局需要视觉复核。')
                elif not (shape.has_text_frame or shape.has_table or shape.has_chart or
                          shape.shape_type in {MSO_SHAPE_TYPE.PICTURE, MSO_SHAPE_TYPE.PLACEHOLDER}):
                    _issue(result, 'unsupported_slide_object', locator, f'此幻灯片对象类型尚未完整解释：{shape.shape_type}')
        visit(slide.shapes, slide_locator)
        if slide.has_notes_slide:
            root = ET.fromstring(slide.notes_slide.part.blob)
            for index, paragraph in enumerate(root.iter(f'{{{_A}}}p'), 1):
                _unit(result, 'speaker_notes', f'{slide_locator}/notes/paragraph:{index}', _xml_content(paragraph), hidden=hidden)
    _package_objects(path, result, 'ppt')
    with zipfile.ZipFile(path) as package:
        for name in package.namelist():
            if not name.startswith(('ppt/slideMasters/', 'ppt/slideLayouts/', 'ppt/notesMasters/', 'ppt/comments/')) or not name.endswith('.xml') or '/_rels/' in name:
                continue
            root = ET.fromstring(package.read(name))
            for index, paragraph in enumerate(root.iter(f'{{{_A}}}p'), 1):
                _unit(result, 'master_or_comment_text', f'part:{name}/paragraph:{index}', _xml_content(paragraph))


def _markdown_view(path: Path, result: dict) -> None:
    """生成面向阅读的衍生视图；不参与源内容完整性判定。"""
    try:
        from markitdown import MarkItDown
    except ImportError as exc:
        _issue(result, 'markdown_converter_unavailable', 'converted.md',
               '缺少 MarkItDown 及对应格式插件，无法生成阅读视图。', dependency=exc.name)
        return
    try:
        converted = MarkItDown(enable_plugins=False).convert(str(path))
        markdown = converted.text_content
        if not isinstance(markdown, str) or not markdown.strip():
            _issue(result, 'markdown_conversion_empty', 'converted.md', '转换器返回空 Markdown，阅读视图未生成。')
            return
        name = path.stem + '.converted.md'
        locator = 'derived:markitdown'
        _unit(result, 'converted_markdown', locator, markdown, derived=True, source_format=path.suffix.lower())
        _asset(result, name, markdown.encode('utf-8'), locator, role='derived_markdown', derived=True,
               formatter='markitdown')
    except Exception as exc:
        _issue(result, 'markdown_conversion_failed', 'converted.md',
               f'MarkItDown 转换失败：{type(exc).__name__}: {exc}')


def extract_document(path: Path) -> dict:
    """完整读取文档，无关键词、段落长度或表头筛选；绝不修改源文档。

    ``units`` 的 content 始终为全文字符串，locator 可回溯源位置；
    ``assets`` 的 data 为原始字节，由调用方归档；``issues`` 均为阻塞缺口。
    ``inventory.complete`` 仅在没有任何已知解析/视觉缺口时为真。
    """
    path = Path(path)
    suffix = path.suffix.lower()
    result = {'units': [], 'assets': [], 'issues': [], 'inventory': {'format': suffix.lstrip('.')}}
    readers = {'.txt': _text, '.md': _text, '.markdown': _text, '.json': _text,
               '.xlsx': _xlsx, '.xlsm': _xlsx, '.docx': _docx, '.pdf': _pdf, '.pptx': _pptx}
    try:
        if suffix not in readers:
            _issue(result, 'unsupported_format', 'file', f'暂不支持 {suffix or "无扩展名"} 格式，需要先提供可稳定解析的开放格式。')
        elif suffix in {'.xlsx', '.xlsm', '.docx', '.pptx'} and path.read_bytes()[:8] == bytes.fromhex('d0cf11e0a1b11ae1'):
            _issue(result, 'encrypted_document', 'file', 'Office 文件为加密或旧二进制容器，需要提供解密后的开放格式。')
        else:
            readers[suffix](path, result)
            if suffix in {'.xlsx', '.xlsm', '.docx'} and not any(
                issue['code'] == 'parse_error' for issue in result['issues']
            ):
                _markdown_view(path, result)
    except ImportError as exc:
        _issue(result, 'dependency_missing', 'file', f'缺少完整解析依赖：{exc.name}')
    except Exception as exc:
        _issue(result, 'parse_error', 'file', f'文档解析失败或未完整结束：{type(exc).__name__}: {exc}')
    result['inventory'].update(
        complete=not result['issues'], unit_count=len(result['units']),
        asset_count=len(result['assets']), issue_count=len(result['issues']),
    )
    return result
