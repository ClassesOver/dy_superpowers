"""评审问题的行为回归：来源、台账及笔记兼容性。"""
import json
import os
import sys
from pathlib import Path

import docx
import pytest
import yaml

PLUGIN = Path(__file__).parents[2]
sys.path.insert(0, str(PLUGIN / "mcp-server/src"))
sys.path.insert(0, str(PLUGIN / "skills/module-research/scripts"))

import research_incremental as ri
import research_sources as rs
import server
from dy_superpowers_mcp.memory import list_notes, read_note, write_note
from dy_superpowers_mcp.project_wiki import configure_project, load_staged, stage_wiki
from dy_superpowers_mcp.research import write_record
from dy_superpowers_mcp.trace import TraceStore


def _candidate(**overrides):
    return {"check_order": "1", "research_id": "01", "config_item": "应用安装",
            "conclusion": "需要安装。", "applicability": "医院A", "suggested_evidence": "旧依据"} | overrides


def _write(workspace, row, module="expense"):
    return write_record(workspace, {"module": module, "check_order": row["check_order"],
        "research_id": row["research_id"], "config_item": row["config_item"],
        "conclusion": row["conclusion"], "scope": row.get("scope", row.get("applicability", "")),
        "evidence_ref": row["suggested_evidence"], "confirmed_by": "测试确认人",
        "confirmed_at": "2026-10-26", "confirmed": True})


@pytest.mark.parametrize("unsafe,suffix", [(unsafe, suffix) for unsafe in ["https://alice:fake-secret@example.com/file",
    "HTTPS://alice%40example.com:fake-secret@example.com/file",
    "https://:fake-secret@example.com/file", "//alice:fake-secret@example.com/file",
    "https:\n//alice:fake-secret@example.com/file"] for suffix in [".json", ".txt"]
    if "\n" not in unsafe or suffix == ".json"])
def test_rejected_attachment_never_enters_candidates_or_trace(tmp_path, monkeypatch, suffix, unsafe):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(server, "initialize_telemetry", lambda: False)
    source = tmp_path / ("qa" + suffix)
    safe = "https://example.com/public"
    if suffix == ".json":
        source.write_text(json.dumps([{"question": "安装要求？", "answer": "需要安装。",
            "attachments": [unsafe, safe]}]), encoding="utf-8")
    else:
        source.write_text(f"问：安装要求？\n答：需要安装。\n附件：{unsafe}\n附件：{safe}\n", encoding="utf-8")
    parsed = rs.parse_source(source, "expense")
    assert "fake-secret" not in json.dumps(parsed)
    assert parsed["records"][0]["suggested_evidence"] == safe
    assert [ref["attachment"] for ref in parsed["records"][0]["detail_refs"]] == [safe]
    assert any(issue["code"] == "attachment_rejected" for issue in parsed["issues"])
    applied, unmatched = ri.apply_mapping(parsed["records"], [
        {"question": "安装要求？", "check_order": "1", "research_id": "01", "scope": "医院A"}])
    assert not unmatched
    entry = ri.classify(tmp_path, "expense", applied)["new"][0]
    _, error = server.execute_tool("dy_research_record_write", {
        "trace_id": "attachment-test", "module": "expense",
        **{key: entry[key] for key in ("check_order", "research_id", "config_item",
                                      "conclusion", "scope", "evidence_ref")},
        "confirmed_by": "测试确认人", "confirmed_at": "2026-10-26", "confirmed": True}, tmp_path)
    assert error is None
    events = TraceStore(tmp_path).read("attachment-test")
    assert events and "fake-secret" not in json.dumps(events)
    assert safe in json.dumps(events)


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


@pytest.mark.parametrize("location", ["suggested_evidence", "detail_refs"])
@pytest.mark.parametrize("unsafe", ["https://alice:fake-secret@example.com/file",
    "https://alice:fake-\nsecret@example.com/file", "//alice:fake-secret@example.com/file",
    "https://alice:fake secret@example.com/file"])
def test_cached_candidates_with_credentials_are_rejected(tmp_path, location, unsafe):
    safe = "https://example.com/public"
    row = _candidate(suggested_evidence=safe, detail_refs=[{"attachment": safe, "fetched": False}])
    if location == "suggested_evidence":
        row[location] = safe + "；" + unsafe
    else:
        row[location].append({"attachment": unsafe, "fetched": False})
    candidates = tmp_path / "candidates.json"
    candidates.write_text(json.dumps({"records": [row]}), encoding="utf-8")
    with pytest.raises(ValueError, match="凭据|重新解析") as error:
        ri._candidates_from(None, candidates, "expense")
    assert "fake-secret" not in str(error.value)


def test_cached_safe_candidates_remain_usable(tmp_path):
    row = _candidate(suggested_evidence="https://example.com/public",
        detail_refs=[{"attachment": "https://example.com/public", "fetched": False}])
    candidates = tmp_path / "candidates.json"
    candidates.write_text(json.dumps({"records": [row], "issues": [],
        "coverage": {"source_subitems": 1}}), encoding="utf-8")
    assert ri._candidates_from(None, candidates, "expense")[0] == [row]


def test_qa_source_scope_is_preserved_as_import_scope(tmp_path):
    source = tmp_path / "qa.json"
    source.write_text(json.dumps({"items": [{"question": "医院范围？", "answer": "需要安装。",
        "scope": "医院A/2026"}]}), encoding="utf-8")
    row = rs.parse_source(source, "expense")["records"][0]
    mapped, unmatched = ri.apply_mapping([row], [{"question": "医院范围？", "check_order": "1",
        "research_id": "01", "scope": "错误映射范围"}])
    assert not unmatched
    assert mapped[0]["scope"] == "医院A/2026"
    assert ri.classify(tmp_path, "expense", mapped)["blocked"] == []
    assert ri.classify(tmp_path, "expense", mapped)["new"][0]["scope"] == "医院A/2026"


def test_record_import_requires_original_source_for_cached_candidates(tmp_path, monkeypatch, capsys):
    row = _candidate(check_order="1", research_id="01", scope="医院A")
    _write(tmp_path, row)
    candidates = tmp_path / "edited.json"
    candidates.write_text(json.dumps({"schema": "dy-research-source/v1", "module": "expense",
        "source_file": "qa.json", "source_sha256": "original-hash", "records": [row],
        "issues": [], "coverage": {"source_subitems": 1}}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["research_incremental.py", str(tmp_path), "--module",
        "expense", "--candidates", str(candidates), "--record"])
    ri.main()
    result = json.loads(capsys.readouterr().out)
    assert result["recorded"] is False
    assert not ri.ledger_path(tmp_path, "expense").exists()


def test_record_import_rejects_candidate_content_changed_since_source_parse(tmp_path, monkeypatch, capsys):
    source = tmp_path / "qa.json"
    source.write_text(json.dumps({"items": [{"question": "安装？", "answer": "需要安装。",
        "scope": "医院A"}]}), encoding="utf-8")
    parsed = rs.parse_source(source, "expense")
    row = parsed["records"][0] | {"check_order": "1", "research_id": "01",
        "conclusion": "被修改的结论。"}
    _write(tmp_path, row)
    candidates = tmp_path / "candidates.json"
    candidates.write_text(json.dumps(parsed | {"records": [row]}), encoding="utf-8")
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps([{ "question": "安装？", "check_order": "1", "research_id": "01"}]),
        encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["research_incremental.py", str(tmp_path), "--module",
        "expense", "--source", str(source), "--candidates", str(candidates), "--map", str(mapping), "--record"])
    ri.main()
    result = json.loads(capsys.readouterr().out)
    assert result["recorded"] is False
    assert not ri.ledger_path(tmp_path, "expense").exists()


def test_record_import_accepts_unmodified_cached_candidates_with_source(tmp_path, monkeypatch, capsys):
    source = tmp_path / "qa.json"
    source.write_text(json.dumps({"items": [{"question": "安装？", "answer": "需要安装。",
        "scope": "医院A"}]}), encoding="utf-8")
    parsed = rs.parse_source(source, "expense")
    candidates = tmp_path / "candidates.json"
    candidates.write_text(json.dumps(parsed), encoding="utf-8")
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps([{"question": "安装？", "check_order": "1", "research_id": "01"}]),
        encoding="utf-8")
    row = parsed["records"][0] | {"check_order": "1", "research_id": "01"}
    _write(tmp_path, row)
    monkeypatch.setattr(sys, "argv", ["research_incremental.py", str(tmp_path), "--module",
        "expense", "--source", str(source), "--candidates", str(candidates), "--map", str(mapping), "--record"])
    ri.main()
    result = json.loads(capsys.readouterr().out)
    assert result["recorded"] is True
    assert ri.ledger_path(tmp_path, "expense").is_file()


@pytest.mark.parametrize("suffix", [".json", ".txt"])
def test_long_questions_map_without_prefix_collisions(tmp_path, suffix):
    questions = ["请确认本次上线范围内各医院费用基础应用的安装启用情况及业务适用性" * 2 + end
                 for end in ["医院A？", "医院B？"]]
    source = tmp_path / ("qa" + suffix)
    if suffix == ".json":
        content = json.dumps([{"question": q, "answer": "需要安装。"} for q in questions])
    else:
        content = "\n".join(f"问：{q}\n答：需要安装。" for q in questions)
    source.write_text(content, encoding="utf-8")
    rows = rs.parse_source(source, "expense")["records"]
    mapped, unmatched = ri.apply_mapping(rows, [
        {"question": q, "check_order": str(i), "research_id": "01"}
        for i, q in enumerate(questions, 1)])
    assert not unmatched
    assert [row["check_order"] for row in mapped] == ["1", "2"]
    assert [row["locator"]["question"] for row in mapped] == questions


def test_unmapped_qa_does_not_append_success_ledger(tmp_path, monkeypatch, capsys):
    row = _candidate()
    _write(tmp_path, row)
    assert ri.record_import(tmp_path, "expense", "qa.json", "old", [row])["recorded"]
    ledger = ri.ledger_path(tmp_path, "expense")
    before = ledger.read_bytes()
    source = tmp_path / "qa.json"
    source.write_text(json.dumps([
        {"question": "应用安装", "answer": "需要安装。", "attachments": ["旧依据"]},
        {"question": "其他问题", "answer": "尚未映射。"}]), encoding="utf-8")
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps([{"question": "应用安装", "check_order": "1",
        "research_id": "01", "scope": "医院A"}]), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["research_incremental.py", str(tmp_path), "--module",
        "expense", "--source", str(source), "--map", str(mapping), "--record"])
    ri.main()
    assert json.loads(capsys.readouterr().out)["recorded"] is False
    assert ledger.read_bytes() == before


def test_removed_source_records_compare_scopes(tmp_path):
    rows = [_candidate(scope=scope) for scope in ["医院A", "医院B"]]
    for row in rows:
        _write(tmp_path, row)
    assert ri.record_import(tmp_path, "expense", "qa.json", "old", rows)["recorded"]
    removed = ri.removed_in_source(tmp_path, "expense", "qa.json", [rows[0]])
    assert len(removed) == 1 and removed[0]["scope"] == "医院B"
    assert ri.removed_in_source(tmp_path, "expense", "qa.json", rows) == []


@pytest.mark.parametrize("field,value", [("suggested_evidence", "新依据"), ("config_item", "新的配置项")])
def test_record_import_requires_all_changed_fields_to_match(tmp_path, field, value):
    row = _candidate()
    _write(tmp_path, row)
    assert ri.record_import(tmp_path, "expense", "qa.json", "old", [row])["recorded"]
    ledger = ri.ledger_path(tmp_path, "expense")
    before = ledger.read_bytes()
    changed = row | {field: value}
    assert len(ri.classify(tmp_path, "expense", [changed])["changed"]) == 1
    assert ri.record_import(tmp_path, "expense", "qa.json", "new", [changed])["recorded"] is False
    assert ledger.read_bytes() == before


def test_note_frontmatter_roundtrips_arbitrary_strings(tmp_path):
    title = '费用政策: 住宿 [政策], "特殊"'
    tags = ["a,b", "[政策]", "true", "a: b"]
    refs = ["https://example.com/file?a=1,b=2", "[附件]", "null"]
    path = write_note(tmp_path, "policy", title, "正文", tags, confirmed=True, source_refs=refs)
    metadata = yaml.safe_load(path.read_text(encoding="utf-8").split("---", 2)[1])
    assert metadata["title"] == title
    assert metadata["tags"] == tags
    assert metadata["source_refs"] == refs


def test_hierarchical_notes_keep_full_identifiers(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(server, "initialize_telemetry", lambda: False)
    result, error = server.execute_tool("dy_notes_write", {"trace_id": "notes-test",
        "slug": "expense/policy", "title": "费用政策", "body": "正文", "confirmed": True}, tmp_path)
    assert error is None and result["slug"] == "expense/policy"
    assert list_notes(tmp_path) == ["expense/policy"]
    assert "正文" in read_note(tmp_path, result["slug"])
    missing, error = server.execute_tool("dy_notes_read", {"trace_id": "notes-test", "slug": "missing"}, tmp_path)
    assert error is None and missing["notes"] == ["expense/policy"]


def test_notes_trace_records_page_and_redacted_content(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(server, "initialize_telemetry", lambda: False)
    args = {"trace_id": "notes-test", "slug": "expense/policy", "title": "费用政策",
            "body": "政策正文\npassword=synthetic-secret", "confirmed": True}
    _, error = server.execute_tool("dy_notes_write", args, tmp_path)
    assert error is None
    _, error = server.execute_tool("dy_notes_read", {"trace_id": "notes-test", "slug": args["slug"]}, tmp_path)
    assert error is None
    events = [e for e in TraceStore(tmp_path).read("notes-test") if e["event"] in {"notes.write", "notes.read"}]
    assert len(events) == 2
    for event in events:
        assert event["memory_file"] == ".dy_superpowers/notes/expense/policy.md"
        assert "政策正文" in event["memory_content"] and "synthetic-secret" not in event["memory_content"]
        assert "[REDACTED]" in event["memory_content"]


def test_wiki_loads_all_legal_module_identifiers_without_collisions(tmp_path):
    configure_project(tmp_path, "测试项目", "demo", confirmed=True)
    for module in ["dy_expense", "dy-expense", "dy--expense", "dy-expense_"]:
        _write(tmp_path, _candidate(), module=module)
    stage_wiki(tmp_path)
    manifest, pages = load_staged(tmp_path)
    assert len(pages) == 9
    assert len({entry["slug"] for entry in manifest["entries"]}) == 9


@pytest.mark.parametrize("raw,expected", [('"abc\'"', "abc'"), ("'abc\"'", 'abc"'),
    ("abc'", "abc'"), ('""abc""', '"abc"'), ("'abc'", "abc"), ('"abc"', "abc")])
def test_env_only_removes_one_matching_quote_pair(tmp_path, monkeypatch, raw, expected):
    monkeypatch.delenv("ODOO_PASSWORD", raising=False)
    (tmp_path / ".env").write_text(f"ODOO_PASSWORD={raw}\n", encoding="utf-8")
    assert server.load_workspace_env(tmp_path) == ["ODOO_PASSWORD"]
    assert os.environ["ODOO_PASSWORD"] == expected
