"""评审问题的行为回归：来源、台账及笔记兼容性。"""
import os
import sys
from pathlib import Path

import docx
import pytest
import yaml

PLUGIN = Path(__file__).parents[2]
sys.path.insert(0, str(PLUGIN / "mcp-server/src"))
sys.path.insert(0, str(PLUGIN / "skills/module-research/scripts"))

import research_sources as rs
import server


def _candidate(**overrides):
    return {"check_order": "1", "research_id": "01", "config_item": "应用安装",
            "conclusion": "需要安装。", "applicability": "医院A", "suggested_evidence": "旧依据"} | overrides


def test_docx_table_cells_generate_candidates(tmp_path):
    document = docx.Document()
    cells = document.add_table(rows=1, cols=3).rows[0].cells
    for cell, value in zip(cells, ["1", "应用安装", "01｜需要安装。"]):
        cell.text = value
    source = tmp_path / "research.docx"
    document.save(source)
    row = rs.parse_source(source, "expense")["records"][0]
    assert (row["check_order"], row["config_item"], row["research_id"], row["conclusion"]) == (
        "1", "应用安装", "01", "01｜需要安装。")


def test_double_slash_local_attachment_still_parses(tmp_path):
    attachment = tmp_path / "attachment.txt"
    attachment.write_text("本地附件的有效结论。", encoding="utf-8")
    value = "/" + str(attachment)
    evidence, refs = rs._attachments([value], [], tmp_path)
    assert evidence and refs[0]["fetched"] is True
    assert refs[0]["rows"] == 1


@pytest.mark.parametrize("raw,expected", [('"abc\'"', "abc'"), ("'abc\"'", 'abc"'),
    ("abc'", "abc'"), ('""abc""', '"abc"'), ("'abc'", "abc"), ('"abc"', "abc")])
def test_env_only_removes_one_matching_quote_pair(tmp_path, monkeypatch, raw, expected):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    (tmp_path / ".env").write_text(f"APP_PASSWORD={raw}\n", encoding="utf-8")
    assert server.load_workspace_env(tmp_path) == ["APP_PASSWORD"]
    assert os.environ["APP_PASSWORD"] == expected
