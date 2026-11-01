"""验证 HRP 源码索引与可检索知识覆盖。"""
import ast
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).parents[2]
SOURCE_ROOT = Path(os.environ.get("DY_HRP_SOURCE_ROOT", "/home/junge/pros/dypowers/data"))
sys.path.insert(0, str(PLUGIN / "mcp-server/src"))


def _manifest_name(path: Path) -> str:
    ast.literal_eval(path.read_text(encoding="utf-8"))
    return path.parent.name


@pytest.mark.skipif(not SOURCE_ROOT.is_dir(), reason="需要 HRP 源码目录 DY_HRP_SOURCE_ROOT")
def test_source_index_matches_all_current_manifests():
    # Knowledge 的 HRP 业务源码范围；Odoo 运行核心和额外解包副本不混入同一版本。
    manifests = sorted(SOURCE_ROOT.glob("dy-*-dev/**/__manifest__.py"))
    expected = {_manifest_name(path) for path in manifests}
    index = (PLUGIN / "references/hrp-source-index.md").read_text(encoding="utf-8")
    actual = set(re.findall(r"^\| `([a-zA-Z0-9_]+)` \|", index, re.MULTILINE))

    assert manifests
    assert actual == expected
    assert len(re.findall(r"^## dy-", index, re.MULTILINE)) == len({path.parents[1].name for path in manifests})
    assert "不在本索引源码范围内" not in index


@pytest.mark.skipif(not SOURCE_ROOT.is_dir(), reason="需要 HRP 源码目录 DY_HRP_SOURCE_ROOT")
def test_packaged_index_reads_every_hrp_manifest_from_current_source():
    from dy_superpowers_mcp import knowledge

    manifests = sorted(SOURCE_ROOT.glob('dy-*-dev/**/__manifest__.py'))
    assert manifests
    for path in manifests:
        info = knowledge.module_info(path.parent.name)
        relative = path.relative_to(SOURCE_ROOT).as_posix()
        references = [row['reference'] for row in info['references'] if row['path'] == relative]
        assert len(references) == 1, relative
        result = knowledge.read(references[0], limit=16000)
        assert result['complete'], relative
        assert result['content'].encode('utf-8') == path.read_bytes(), relative


def test_packaged_index_contains_and_reads_all_audited_source_files():
    from dy_superpowers_mcp import knowledge

    audit = PLUGIN.parents[1] / 'docs/research-sources/zoekt/hrp-20261030-excluded'
    expected = json.loads((audit / 'content-sha256.json').read_text())
    service = knowledge._source_service()
    listing = service.request('/api/search', {'Q': 'type:file', 'Opts': {
        'MaxDocDisplayCount': len(expected) + 1, 'MaxMatchDisplayCount': len(expected) + 1,
        'ShardMaxMatchCount': 100000, 'TotalMaxMatchCount': 100000,
        'MaxWallTime': 2000000000}})['Result']
    assert not listing.get('FilesSkipped') and not listing.get('ShardsSkipped')
    assert {row['FileName'] for row in listing.get('Files') or []} == set(expected)
    for path, digest in expected.items():
        # 原生 Whole 回读避免对超大文件按字符分页时反复传输整个文件。
        expression = 'file:' + knowledge._quote('^' + knowledge._literal(path) + '$')
        result = knowledge._query_result(service, expression, 2, whole=True)
        assert not result.get('FilesSkipped') and not result.get('ShardsSkipped'), path
        rows = result.get('Files') or []
        assert len(rows) == 1 and rows[0]['FileName'] == path, path
        content = base64.b64decode(rows[0].get('Content') or '')
        assert hashlib.sha256(content).hexdigest() == digest, path


@pytest.mark.skipif(not SOURCE_ROOT.is_dir(), reason="需要 HRP 源码目录 DY_HRP_SOURCE_ROOT")
def test_current_hrp_projection_matches_packaged_file_set_and_content(tmp_path):
    root = PLUGIN.parents[1]
    audit = root / 'docs/research-sources/zoekt/hrp-20261030-excluded'
    expected = json.loads((audit / 'content-sha256.json').read_text())
    result = subprocess.run([
        'rsync', '-rni', '--out-format=%i %n',
        '--filter=merge ' + str(root / 'scripts/zoekt-source.filter'),
        str(SOURCE_ROOT) + '/', str(tmp_path / 'projection') + '/'],
        capture_output=True, text=True, check=True)
    actual = {line.split(' ', 1)[1] for line in result.stdout.splitlines() if line.startswith('>f')}
    assert actual == set(expected)
    conversions = {row['path']: row['original_encoding'] for row in (
        json.loads(line) for line in (audit / 'encoding-conversions.jsonl').read_text().splitlines())}
    for path, digest in expected.items():
        content = (SOURCE_ROOT / path).read_bytes()
        if path in conversions:
            content = content.decode(conversions[path]).encode('utf-8')
        assert hashlib.sha256(content).hexdigest() == digest, path


def test_knowledge_search_covers_review_topics_and_model_entrypoints():
    from dy_superpowers_mcp import knowledge

    expected = {
        "OCR": "ocr_expense",
        "银医": "dy_bank_expense",
        "移动费用": "mobile_expense",
        "鼎医采购": "dy_purchase",
        "dy.expense.report": "dy_expense",
        "dy.expense.report.payment": "dy_expense",
        "dy.expense.payment": "dy_expense",
        "dy.expense.policy": "dy_expense",
        "dy.expense.policy.line": "dy_expense",
    }
    for query, module in expected.items():
        result = knowledge.search(query)
        assert module in {item["module"] for item in result["results"]}, query

    assert {"dy.expense.report", "dy.expense.report.payment", "dy.expense.policy", "dy.expense.policy.line"} <= set(
        knowledge.module_info("dy_expense")["models"]
    )


def test_knowledge_distinguishes_periodic_vouchers_from_carry_forward():
    from dy_superpowers_mcp import knowledge

    workflow = knowledge.module_info("dy_fin_acc")["workflow"]
    assert "周期凭证模板结转" not in workflow
    assert "执行记录" in workflow
    assert "经常性凭证" in workflow


@pytest.mark.skipif(not SOURCE_ROOT.is_dir(), reason="需要 HRP 源码目录 DY_HRP_SOURCE_ROOT")
def test_new_knowledge_dependencies_match_source_manifests():
    from dy_superpowers_mcp import knowledge

    for module in ["ocr_expense", "dy_bank_base", "dy_bank_expense", "mobile_expense", "dy_purchase"]:
        info = knowledge.module_info(module)
        manifest = SOURCE_ROOT / (info["source_repo"] + "-dev") / module / "__manifest__.py"
        assert info["depends"] == ast.literal_eval(manifest.read_text(encoding="utf-8"))["depends"]
        assert "不证明线上安装状态或工作流" in info["limitations"]
