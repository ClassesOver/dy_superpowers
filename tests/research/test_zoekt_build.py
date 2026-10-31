"""原生 Zoekt 构建入口的验收；工具通过环境变量显式提供。"""
import json
import os
from pathlib import Path
import subprocess
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def tools():
    names = ('ZOEKT_INDEX_BIN', 'ZOEKT_SERVER_BIN', 'GITLEAKS_BIN')
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        pytest.skip('需要开发期原生工具：' + ', '.join(missing))
    return dict(os.environ)


@pytest.fixture
def audit():
    directory = ROOT / 'docs/research-sources/zoekt'
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='pytest-', dir=directory) as location:
        yield Path(location)


def build(source, output, audit, tools):
    return subprocess.run(['bash', str(ROOT / 'scripts/build-zoekt-index.sh'),
                           '--source', str(source), '--output', str(output), '--audit', str(audit)],
                          cwd=ROOT, env=tools, capture_output=True, text=True, timeout=90)


def test_projection_only_includes_hrp_business_repositories(tmp_path):
    source = tmp_path / 'source'
    for name in ['dy-demo-dev/demo/rule.py', 'dy-odoo/addons/base/rule.py',
                 'unpacked-copy/dy-demo-dev/demo/rule.py', 'odoo-forge/tool.py']:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# 合成源码\n')
    result = subprocess.run(['rsync', '-rni', '--out-format=%i %n',
        '--filter=merge ' + str(ROOT / 'scripts/zoekt-source.filter'),
        str(source) + '/', str(tmp_path / 'projection') + '/'],
        capture_output=True, text=True, check=True)
    files = {line.split(' ', 1)[1] for line in result.stdout.splitlines() if line.startswith('>f')}
    assert files == {'dy-demo-dev/demo/rule.py'}


def test_native_build_preserves_complete_text_without_original_source(tmp_path, tools, audit):
    source = tmp_path / 'source'
    addon = source / 'dy-demo-dev' / 'demo'
    addon.mkdir(parents=True)
    (addon / '__manifest__.py').write_text("{'name': '费用测试', 'depends': ['base']}\n")
    (addon / 'rule.py').write_text('class Rule:\n    company_name = "报销公司"\n')
    (addon / 'empty.py').write_bytes(b'')
    (addon / 'large.js').write_text('// 完整大文件\n' + 'console.log("example");\n' * 100000)
    (addon / 'legacy.xml').write_bytes('<?xml version="1.0" encoding="GB2312"?><odoo>银行代码</odoo>'.encode('gb2312'))
    (addon / '.env').write_text('PRIVATE_SENTINEL=must-not-include')
    (addon / 'outside.py').symlink_to('/etc/hostname')
    output = tmp_path / 'index'
    result = build(source, output, audit, tools)
    assert result.returncode == 0, result.stderr + result.stdout
    assert list(output.glob('*.zoekt'))
    report = json.loads((audit / 'verification.json').read_text())
    assert report['status'] == 'passed'
    assert report['verified_files'] == 5
    assert report['content_mismatches'] == []
    assert json.loads((output / 'bundle.json').read_text())['status'] == 'verified'
    # 构建验收也必须验证可脱离源码目录，不能只核对目录内的文件。
    assert report['source_dependency'] is False
    assert (addon / 'legacy.xml').read_bytes().decode('gb2312').endswith('</odoo>')


def test_credentials_stop_build_and_preserve_existing_output(tmp_path, tools, audit):
    source = tmp_path / 'source'
    source.mkdir()
    # 官方 GitHub token 规则的隔离合成测试数据，不对应任何真实凭据。
    addon = source / 'dy-demo-dev'
    addon.mkdir()
    (addon / 'example.py').write_text('sample = "ghp_' + 'aB3dE5fG7hJ9kL1mN2pQ4rS6tU8vW0xY2zA4' + '"\n')
    output = tmp_path / 'index'
    output.mkdir()
    (output / 'previous.zoekt').write_bytes(b'previous-valid-artifact')
    result = build(source, output, audit, tools)
    assert result.returncode != 0
    assert (output / 'previous.zoekt').read_bytes() == b'previous-valid-artifact'
    report = json.loads((audit / 'scan.json').read_text())
    assert report['status'] == 'blocked'
    assert report['findings']
    assert 'ghp_' not in (audit / 'scan.json').read_text()


def test_authorized_sensitive_files_are_excluded_by_exact_path(tmp_path, tools, audit):
    source = tmp_path / 'source'
    excluded = [
        'dy-base-dev/dy_base/controllers/ureport_package.py',
        'dy-base-dev/dy_base/docs/ureport_manager_api.md',
        'dy-base-dev/dy_base/models/dy_ureport_manager.py',
        'dy-base-dev/dy_base/models/model.py',
        'dy-base-dev/dy_base/static/src/js/fields/char_select.js',
        'dy-base-dev/dy_base/static/src/lib/ofd/js/chunk-vendors.82b33c31.js.map',
        'dy-base-dev/dy_base/static/src/lib/xlsx/jszip.js',
        'dy-base-dev/dy_base/static/src/lib/xlsx/newTableExport.js',
        'dy-base-dev/dy_onlyoffice/docker-compose.yml',
        'dy-base-dev/online_helper/models/online_help.py',
        'dy-base-dev/store_encrypt/models/engine.py',
        'dy-mobile-service-dev/ocr_base/models/dy_ocr_interface_config.py',
    ]
    # 隔离合成凭据；任一已授权路径漏排除都会被真实扫描器阻止。
    for name in excluded:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('sample = "ghp_' + 'aB3dE5fG7hJ9kL1mN2pQ4rS6tU8vW0xY2zA4' + '"\n')
    retained = [
        'dy-base-dev/dy_base/models/model_extra.py',
        'dy-other-dev/dy_base/models/model.py',
        'dy-other-dev/nested/dy-base-dev/dy_base/models/model.py',
    ]
    for name in retained:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# 安全的同名或相近路径仍需收录\n')

    output = tmp_path / 'index'
    result = build(source, output, audit, tools)

    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads((audit / 'included-files.json').read_text()) == sorted(retained)
    assert json.loads((audit / 'scan.json').read_text())['findings'] == []
    report = json.loads((audit / 'verification.json').read_text())
    assert report['status'] == 'passed'
    assert report['verified_files'] == 3
    assert all((source / name).exists() for name in excluded)


def test_unknown_encoding_stops_before_publish(tmp_path, tools, audit):
    source = tmp_path / 'source'
    source.mkdir()
    addon = source / 'dy-demo-dev'
    addon.mkdir()
    (addon / 'bad.py').write_bytes(b'print("\xff")\n')
    output = tmp_path / 'index'
    result = build(source, output, audit, tools)
    assert result.returncode != 0
    assert not output.exists()
    assert '编码' in result.stderr
