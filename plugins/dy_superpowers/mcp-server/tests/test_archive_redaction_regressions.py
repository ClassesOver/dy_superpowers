"""脱敏保留业务正文，文本资产不能绕过同一边界。"""
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
from dy_superpowers_mcp.source_archive import archive_source
from dy_superpowers_mcp.trace import redact


@pytest.mark.parametrize('body, expected', [
    ('password: synthetic-secret；基础应用仍须上线', 'password: [REDACTED]；基础应用仍须上线'),
    ('password: synthetic secret；中文业务结论', 'password: [REDACTED]；中文业务结论'),
    ('password: synthetic 中文密码 tail\n业务正文', 'password: [REDACTED]\n业务正文'),
    ('password: synthetic\"secret<tail>\n业务正文', 'password: [REDACTED]\n业务正文'),
    ('confirmed_by: Employee Name\n业务正文', 'confirmed_by: [REDACTED]\n业务正文'),
    ('password: "synthetic,secret;tail"；业务正文', 'password: "[REDACTED]"；业务正文'),
    ('password: ["synthetic,secret", "other"]；业务正文', 'password: [REDACTED]；业务正文'),
    ('password: {"value": "synthetic-secret"}；业务正文', 'password: [REDACTED]；业务正文'),
    ('password: synthetic[123]tail；业务正文', 'password: [REDACTED]；业务正文'),
    ('密码: "synthetic secret"；业务正文', '密码: "[REDACTED]"；业务正文'),
    ('密码: synthetic secret；业务正文', '密码: [REDACTED]；业务正文'),
])
def test_redaction_preserves_business_suffix_and_is_idempotent(body, expected):
    assert redact(body) == expected
    assert redact(expected) == expected


def test_json_string_with_inline_secret_remains_valid_json():
    body = json.dumps({'answer': 'password: synthetic-secret 手机号：13800138000 基础应用必需'}, ensure_ascii=False)
    safe = redact(body)
    assert 'synthetic-secret' not in safe
    assert '基础应用必需' in json.loads(safe)['answer']


def test_chinese_credential_keys_redact_structured_and_serialized_values():
    value = {'密码': 'synthetic-secret', '嵌套': {'密钥': ['synthetic-token']}, '业务': '保留'}
    expected = {'密码': '[REDACTED]', '嵌套': {'密钥': '[REDACTED]'}, '业务': '保留'}
    assert redact(value) == expected
    assert json.loads(redact(json.dumps(value, ensure_ascii=False))) == expected


def test_archive_redacts_split_xml_runs_and_attributes_without_corrupting_xml(tmp_path):
    source = tmp_path / 'source.txt'
    source.write_text('来源仍保留', encoding='utf-8')
    raw = ('<document><p><r><t>password: </t></r><r><t>synthetic-secret</t></r></p>'
           '<p><t>业务尾部仍保留</t></p><item value="token=synthetic-token" password="synthetic-attribute"/></document>').encode()

    result = archive_source(tmp_path, source, 'expense', extractor=lambda _: {
        'units': [], 'issues': [], 'inventory': {},
        'assets': [{'name': 'document.xml', 'data': raw, 'locator': 'part:document.xml'}],
    })

    run = tmp_path / result['path']
    asset = next((run / 'assets').glob('*.xml'))
    data = asset.read_bytes()
    root = ET.fromstring(data)
    assert b'synthetic-secret' not in data and b'synthetic-token' not in data
    assert b'synthetic-attribute' not in data
    assert '业务尾部仍保留' in ''.join(root.itertext())
    assert '[REDACTED]' in ''.join(root.itertext())
    saved = json.loads((run / 'content.json').read_text())['assets'][0]
    assert saved['redacted'] is True
    assert source.read_text() == '来源仍保留'


def test_unreadable_text_asset_is_not_written_as_unredacted_bytes(tmp_path):
    source = tmp_path / 'invalid.txt'
    original = b'password=synthetic-secret\xff'
    source.write_bytes(original)

    result = archive_source(tmp_path, source, 'expense')

    run = tmp_path / result['path']
    assert result['status'] == 'partial'
    assert not list((run / 'assets').iterdir())
    issues = json.loads((run / 'issues.json').read_text())
    assert {'text_encoding_unknown', 'asset_text_redaction_failed'} <= {issue['code'] for issue in issues}
    assert source.read_bytes() == original


def test_binary_asset_preserves_bytes_without_claiming_redaction(tmp_path):
    source = tmp_path / 'source.txt'
    source.write_text('来源', encoding='utf-8')
    raw = b'\x00\x01binary asset'
    result = archive_source(tmp_path, source, 'expense', extractor=lambda _: {
        'units': [], 'issues': [{'code': 'embedded_object_unparsed', 'blocking': True}],
        'inventory': {}, 'assets': [{'name': 'object.bin', 'data': raw, 'locator': 'embedding:1'}],
    })
    run = tmp_path / result['path']
    asset = json.loads((run / 'content.json').read_text())['assets'][0]
    assert (run / asset['path']).read_bytes() == raw
    assert asset['redacted'] is False
    assert result['status'] == 'partial'


def test_xml_break_preserves_following_business_line(tmp_path):
    source = tmp_path / 'source.txt'
    source.write_text('来源', encoding='utf-8')
    raw = b'<document><p><r><t>password: </t></r><r><t>synthetic-secret</t><br/><t>Business survives</t></r></p></document>'
    result = archive_source(tmp_path, source, 'expense', extractor=lambda _: {
        'units': [], 'issues': [], 'inventory': {},
        'assets': [{'name': 'document.xml', 'data': raw, 'locator': 'part:document.xml'}],
    })
    asset = next((tmp_path / result['path'] / 'assets').glob('*.xml'))
    root = ET.fromstring(asset.read_bytes())
    text = ''.join(root.itertext())
    assert 'synthetic-secret' not in text
    assert 'Business survives' in text


def test_xml_tab_between_password_label_and_value_is_redacted(tmp_path):
    source = tmp_path / 'source.txt'
    source.write_text('来源', encoding='utf-8')
    raw = b'<document><p><r><t>password:</t><tab/><t>synthetic-secret</t></r></p></document>'
    result = archive_source(tmp_path, source, 'expense', extractor=lambda _: {
        'units': [], 'issues': [], 'inventory': {},
        'assets': [{'name': 'document.xml', 'data': raw, 'locator': 'part:document.xml'}],
    })
    asset = next((tmp_path / result['path'] / 'assets').glob('*.xml'))
    assert b'synthetic-secret' not in asset.read_bytes()
    assert '[REDACTED]' in ''.join(ET.fromstring(asset.read_bytes()).itertext())
