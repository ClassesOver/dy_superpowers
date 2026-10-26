"""增量导入复审：解析完整性、移除处置与 QA 映射歧义。"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from dy_superpowers_mcp.research import read_record, write_record

PLUGIN = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location(
    "incremental_review_followup",
    PLUGIN / "skills/module-research/scripts/research_incremental.py",
)
ri = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ri)


def _cli(workspace, source, monkeypatch, capsys, *, record=False, candidates=False):
    argv = ["research_incremental.py", str(workspace), "--module", "expense",
            "--candidates" if candidates else "--source", str(source),
            "--scope", "示例医院/2026"]
    if record:
        argv.append("--record")
    monkeypatch.setattr(sys, "argv", argv)
    ri.main()
    return json.loads(capsys.readouterr().out)


def test_missing_qa_answer_blocks_diff_and_ledger(tmp_path, monkeypatch, capsys):
    source = tmp_path / "qa.json"
    source.write_text(json.dumps({"items": [{"question": "应用是否启用？"}]}), encoding="utf-8")
    output = _cli(tmp_path, source, monkeypatch, capsys)
    assert output["verdict"] == "delta"
    assert output["blocked"]
    result = _cli(tmp_path, source, monkeypatch, capsys, record=True)
    assert result["recorded"] is False
    assert not ri.ledger_path(tmp_path, "expense").exists()


@pytest.mark.parametrize("issues,coverage", [
    ([{"code": "page_without_text_layer", "page": 1}], {"source_subitems": 0}),
    ([], {"source_subitems": 1}),
])
def test_candidate_metadata_gaps_block_registration(tmp_path, monkeypatch, capsys, issues, coverage):
    source = tmp_path / "parsed.json"
    source.write_text(json.dumps({"records": [], "issues": issues, "coverage": coverage}), encoding="utf-8")
    output = _cli(tmp_path, source, monkeypatch, capsys, candidates=True)
    assert output["blocked"]
    result = _cli(tmp_path, source, monkeypatch, capsys, record=True, candidates=True)
    assert result["recorded"] is False
    assert not ri.ledger_path(tmp_path, "expense").exists()


def test_legacy_candidates_require_reparse(tmp_path):
    source = tmp_path / "legacy.json"
    source.write_text(json.dumps({"records": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="重新解析"):
        ri._candidates_from(None, source, "expense")


@pytest.mark.parametrize("issue", [{"code": "multiple_conclusion_tables", "count": 2},
                                  {"code": "hidden_sheet", "sheet": "隐藏表"}])
def test_fully_parsed_source_hints_are_informational(tmp_path, monkeypatch, capsys, issue):
    source = tmp_path / "parsed.json"
    source.write_text(json.dumps({"records": [], "issues": [issue],
        "coverage": {"source_subitems": 0}}), encoding="utf-8")
    output = _cli(tmp_path, source, monkeypatch, capsys, candidates=True)
    assert not output["blocked"]
    assert output["notes"]
    assert _cli(tmp_path, source, monkeypatch, capsys, record=True, candidates=True)["recorded"]


def _candidate(research_id):
    return {"check_order": "1", "research_id": research_id, "config_item": "应用安装",
            "conclusion": "应用已启用。", "applicability": "示例医院/2026"}


def _write_current(workspace, row, **extra):
    return write_record(workspace, {"module": "expense",
        "check_order": row["check_order"], "research_id": row["research_id"],
        "config_item": row["config_item"], "conclusion": row["conclusion"],
        "scope": row["applicability"], "confirmed_by": "测试确认人",
        "confirmed_at": "2026-10-20", "confirmed": True, **extra})


@pytest.mark.parametrize("remaining", [[], [_candidate("01")]])
def test_removal_requires_confirmed_retirement_before_ledger_advances(tmp_path, remaining):
    original = [_candidate("01"), _candidate("02")]
    for row in original:
        _write_current(tmp_path, row)
    assert ri.record_import(tmp_path, "expense", "source.txt", "old", original)["recorded"]
    ledger = ri.ledger_path(tmp_path, "expense")
    before = ledger.read_bytes()
    result = ri.record_import(tmp_path, "expense", "source.txt", "new", remaining)
    assert result["recorded"] is False
    assert ledger.read_bytes() == before
    removed_ids = {"02"} if remaining else {"01", "02"}
    assert {item["research_id"] for item in result["problems"]} == removed_ids
    for row in original:
        current = read_record(tmp_path, "expense", "1", row["research_id"], scope=row["applicability"])
        assert current["status"] == "confirmed"
        if row["research_id"] in removed_ids:
            _write_current(tmp_path, row, status="retired", retirement_reason="业务确认来源移除。",
                           expected_revision=current["revision"])
    result = ri.record_import(tmp_path, "expense", "source.txt", "new", remaining)
    assert result == {"recorded": True, "keys": len(remaining)}
    assert json.loads(ledger.read_text(encoding="utf-8"))[-1]["source_sha256"] == "new"


@pytest.mark.parametrize("duplicate_candidates", [True, False])
def test_duplicate_question_mapping_is_rejected(duplicate_candidates):
    rows = [{"locator": {"kind": "qa", "question": "应用是否启用？"},
             "conclusion": "已启用。", "applicability": "院区 A"}]
    mapping = [{"question": "应用是否启用？", "check_order": "1", "research_id": "01"}]
    if duplicate_candidates:
        rows.append({**rows[0], "applicability": "院区 B"})
    else:
        mapping.append({**mapping[0], "research_id": "02"})
    with pytest.raises(ValueError, match="重复|歧义"):
        ri.apply_mapping(rows, mapping)


def test_unique_mapping_preserves_source_scope():
    row = {"locator": {"kind": "qa", "question": "应用是否启用？"},
           "conclusion": "已启用。", "applicability": "院区 A"}
    mapping = [{"question": "应用是否启用？", "check_order": "1", "research_id": "01",
                "scope": "院区 B"}]
    applied, unmatched = ri.apply_mapping([row], mapping)
    assert unmatched == []
    assert applied[0]["applicability"] == "院区 A"
    assert applied[0]["check_order"] == "1"
    assert applied[0]["research_id"] == "01"
    assert "check_order" not in row
