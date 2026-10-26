"""验证 HRP 源码索引与可检索知识覆盖。"""
import ast
import os
import re
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
    manifests = sorted(SOURCE_ROOT.glob("*/**/__manifest__.py"))
    expected = {_manifest_name(path) for path in manifests}
    index = (PLUGIN / "references/hrp-source-index.md").read_text(encoding="utf-8")
    actual = set(re.findall(r"^\| `([a-zA-Z0-9_]+)` \|", index, re.MULTILINE))

    assert manifests
    assert actual == expected
    assert len(re.findall(r"^## dy-", index, re.MULTILINE)) == len({path.parents[1].name for path in manifests})
    assert "不在本索引源码范围内" not in index


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

