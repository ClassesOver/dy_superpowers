"""调研来源增量导入回归：差异分类、只写增量、回读校验与导入台账。"""
import importlib.util
import json
import sys
import threading
from pathlib import Path

import pytest

PLUGIN = Path(__file__).parents[2]
sys.path.insert(0, str(PLUGIN / "mcp-server/src"))

from dy_superpowers_mcp.research import read_record, write_record  # noqa: E402


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "research_incremental", PLUGIN / "skills/module-research/scripts/research_incremental.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ri = _load_script()

# parse_plain 解析 .txt：标题行给检查顺序，正文行“01｜结论”给子项编号。
V1 = ["# 1 应用安装", "01｜费用基础应用需要安装并启用。", "02｜OCR 模块按需启用，服务已确认可用。"]
V2 = ["# 1 应用安装", "01｜费用基础应用需要安装，并在验收前启用。",  # 01 结论变更
      "# 999 不存在的检查项", "01｜该行缺少有效键时应阻塞。"]  # 02 被移除；第二条将在测试中去掉键


def _write_source(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _candidates(path: Path) -> list[dict]:
    return ri._candidates_from(path, None, "expense")[0]


def _write_all(workspace, entries):
    for entry in entries:
        write_record(workspace, {"module": "expense", "check_order": entry["check_order"],
                                 "research_id": entry["research_id"], "config_item": entry["config_item"],
                                 "conclusion": entry["conclusion"], "scope": entry["scope"],
                                 "evidence_ref": entry.get("evidence_ref", ""),
                                 "confirmed_by": "测试确认人", "confirmed_at": "2026-10-20",
                                 "confirmed": True})


def test_first_import_all_new_then_record(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    source = _write_source(tmp_path / "v1.txt", V1)
    result = ri.classify(workspace, "expense", _candidates(source), default_scope="示例医院/2026")
    assert not result["changed"] and not result["unchanged"] and not result["blocked"]
    assert [item["research_id"] for item in result["new"]] == ["01", "02"]
    assert result["new"][0]["scope"] == "示例医院/2026"

    _write_all(workspace, result["new"])
    recorded = ri.record_import(workspace, "expense", source.name,
                                ri._candidates_from(source, None, "expense")[2],
                                _candidates(source), default_scope="示例医院/2026")
    assert recorded["recorded"] and recorded["keys"] == 2
    ledger = json.loads(ri.ledger_path(workspace, "expense").read_text(encoding="utf-8"))
    assert len(ledger) == 1 and ledger[0]["source_name"] == "v1.txt" and len(ledger[0]["keys"]) == 2


def test_concurrent_import_registration_preserves_both_ledger_entries(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    barrier = threading.Barrier(2)
    load = ri.load_ledger

    def synchronized_load(*args):
        ledger = load(*args)
        try:
            barrier.wait(timeout=0.2)
        except threading.BrokenBarrierError:
            pass
        return ledger

    monkeypatch.setattr(ri, "load_ledger", synchronized_load)
    results = []
    workers = [threading.Thread(target=lambda name=name: results.append(
        ri.record_import(workspace, "expense", name, name, []))) for name in ("a.txt", "b.txt")]

    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=2)

    ledger = json.loads(ri.ledger_path(workspace, "expense").read_text(encoding="utf-8"))
    assert len(results) == 2 and all(result["recorded"] for result in results)
    assert {entry["source_name"] for entry in ledger} == {"a.txt", "b.txt"}


def test_evidence_reference_discloses_truncated_attachment_locations():
    row = {"detail_refs": [{
        "locations": [{"sheet": "明细", "cell": f"A{index}"} for index in range(1, 51)],
        "location_count": 51,
        "locations_truncated": True,
    }]}

    evidence = ri._evidence_ref(row)

    assert "另有1条附件定位未展开" in evidence


def test_reimport_same_source_is_noop(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    source = _write_source(tmp_path / "v1.txt", V1)
    first = ri.classify(workspace, "expense", _candidates(source), default_scope="示例医院/2026")
    _write_all(workspace, first["new"])
    ri.record_import(workspace, "expense", source.name,
                     ri._candidates_from(source, None, "expense")[2],
                     _candidates(source), default_scope="示例医院/2026")

    again = ri.classify(workspace, "expense", _candidates(source), default_scope="示例医院/2026")
    assert again["unchanged"] and not again["new"] and not again["changed"]
    removed = ri.removed_in_source(workspace, "expense", source.name, _candidates(source))
    assert removed == []  # 同版本重导入：无增量，不产生新修订


def test_new_version_changed_removed_and_blocked(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    v1 = _write_source(tmp_path / "v1.txt", V1)
    first = ri.classify(workspace, "expense", _candidates(v1), default_scope="示例医院/2026")
    _write_all(workspace, first["new"])
    ri.record_import(workspace, "expense", v1.name,
                     ri._candidates_from(v1, None, "expense")[2],
                     _candidates(v1), default_scope="示例医院/2026")

    v2 = _write_source(tmp_path / "v1.txt", V2)  # 同名文件的升级版本
    candidates = _candidates(v2)
    candidates[-1]['check_order'] = ''  # 历史候选缺少键时不能导入。
    result = ri.classify(workspace, "expense", candidates, default_scope="示例医院/2026")
    assert [item["research_id"] for item in result["changed"]] == ["01"]
    changed = result["changed"][0]
    current = read_record(workspace, "expense", "1", "01", scope_id=changed["scope_id"])
    assert changed["expected_revision"] == current["revision"]
    assert changed["current"]["conclusion"] == "费用基础应用需要安装并启用。"
    assert result["unchanged"] == [] and result["new"] == []
    assert [item["research_id"] for item in result["blocked"]] == ["01"]
    assert "缺少检查顺序" in result["blocked"][0]["reasons"]

    removed = ri.removed_in_source(workspace, "expense", v2.name, candidates)
    assert [item["research_id"] for item in removed] == ["02"]
    assert "不自动停用" in removed[0]["note"]


def test_record_fails_closed_until_writes_complete(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    v1 = _write_source(tmp_path / "v1.txt", V1)
    candidates = _candidates(v1)
    first = ri.classify(workspace, "expense", candidates, default_scope="示例医院/2026")
    _write_all(workspace, first["new"][:1])  # 只写 01，02 未写入
    result = ri.record_import(workspace, "expense", v1.name,
                              ri._candidates_from(v1, None, "expense")[2],
                              candidates, default_scope="示例医院/2026")
    assert result["recorded"] is False
    assert [p["research_id"] for p in result["problems"]] == ["02"]
    assert not ri.ledger_path(workspace, "expense").exists()  # 校验失败不记账


def test_scope_resolution_blocked_on_multi_scope_and_missing(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_all(workspace, [
        {"check_order": "1", "research_id": "01", "config_item": "1 应用安装",
         "conclusion": "医院A结论。", "scope": "示例医院/2026"},
    ])
    write_record(workspace, {"module": "expense", "check_order": "1", "research_id": "01",
                             "config_item": "应用安装", "conclusion": "医院B结论。",
                             "scope": "北院区/2026", "confirmed_by": "测试确认人",
                             "confirmed_at": "2026-10-20", "confirmed": True})
    source = _write_source(tmp_path / "v1.txt", V1)
    # 同键两个范围且来源无适用条件、未给默认范围 → 阻塞，不能猜范围
    result = ri.classify(workspace, "expense", _candidates(source))
    assert result["blocked"] and "适用范围" in result["blocked"][0]["reasons"][-1]
    assert not result["new"] and not result["changed"]

    # 明确来源记录范围 → 匹配该范围并分类，不新建重复记录
    candidates = _candidates(_write_source(tmp_path / "v1b.txt", [
        "# 1 应用安装", "01｜医院A结论。"]))
    candidates[0]["scope"] = "示例医院/2026"
    result = ri.classify(workspace, "expense", candidates)
    expected = read_record(workspace, "expense", "1", "01", scope="示例医院/2026")
    assert result["unchanged"] and not result["new"] and not result["changed"]
    assert result["unchanged"][0]["scope_id"] == expected["scope_id"]


def test_pending_source_template_is_blocked_instead_of_classified_as_new(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    candidate = {
        "check_order": "GL-1-001", "research_id": "01", "config_item": "应用安装",
        "conclusion": "基础应用本期必需【待确认】", "applicability": "适用于所有本期公司",
        "contains_pending_questions": True,
        "locator": {"sheet": "总账调研清单", "cell": "G4"},
    }

    result = ri.classify(workspace, "general-ledger", [candidate], default_scope="测试医院/2026")

    assert not result["new"]
    assert not result["changed"]
    assert "来源仍含待确认内容，不能分类为可导入结论" in result["blocked"][0]["reasons"]


def test_checklist_applicability_is_not_used_as_record_scope(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    candidate = {
        "check_order": "GL-1-001", "research_id": "01", "config_item": "应用安装",
        "conclusion": "基础应用本期必需。", "applicability": "适用于所有本期总账公司。",
        "contains_pending_questions": False,
        "locator": {"sheet": "总账调研清单", "cell": "G4"},
    }

    result = ri.classify(workspace, "general-ledger", [candidate], default_scope="测试院区/2026")

    assert result["new"][0]["scope"] == "测试院区/2026"


def test_cli_diff_and_record_output(tmp_path, capsys, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    source = _write_source(tmp_path / "v1.txt", V1)
    monkeypatch.setattr(sys, "argv", ["research_incremental.py", str(workspace),
                                      "--module", "expense", "--source", str(source),
                                      "--scope", "示例医院/2026"])
    ri.main()
    output = json.loads(capsys.readouterr().out)
    assert output["schema"] == "dy-research-incremental/v1" and output["verdict"] == "delta"
    assert output["counts"] == {"new": 2, "changed": 0, "unchanged": 0, "blocked": 0,
                                "removed_in_source": 0}
    assert any("首次导入" in note for note in output["notes"])

    _write_all(workspace, output["new"])
    monkeypatch.setattr(sys, "argv", ["research_incremental.py", str(workspace),
                                      "--module", "expense", "--source", str(source),
                                      "--scope", "示例医院/2026", "--record"])
    ri.main()
    recorded = json.loads(capsys.readouterr().out)
    assert recorded["recorded"] and recorded["keys"] == 2


def _load_sources():
    spec = importlib.util.spec_from_file_location(
        "research_sources", PLUGIN / "skills/module-research/scripts/research_sources.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rs = _load_sources()


def test_qa_json_parse_attachments_and_incremental_flow(tmp_path, capsys, monkeypatch):
    """QA JSON：问题/回答/附件链接 → 未映射阻塞 → 映射后增量写入 → 回读记账。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    # 本地附件：可被解析的 txt；链接附件：原样保留为依据，不做网络请求。
    (tmp_path / "attachment.txt").write_text("# 2 费用政策\n01｜政策依据附件内容。\n", encoding="utf-8")
    qa = tmp_path / "qa.json"
    qa.write_text(json.dumps({"items": [
        {"question": "费用基础应用是否需要安装？", "answer": "需要安装并启用。",
         "attachments": ["https://gitlab.example.com/research/-/blob/main/sources/a.txt"]},
        {"question": "费用政策依据是什么？", "answer": "住宿费不得超标。",
         "attachments": ["attachment.txt"]},
        {"question": "带凭据的链接长什么样？", "answer": "应被拒绝。",
         "attachments": ["https://user:pat@gitlab.example.com/a.txt"]},
    ]}, ensure_ascii=False), encoding="utf-8")

    parsed = rs.parse_source(qa, "expense")
    assert parsed["coverage"]["source_subitems"] == 3
    link_row, local_row = parsed["records"][0], parsed["records"][1]
    assert link_row["suggested_evidence"].startswith("https://gitlab.example.com")
    assert link_row["detail_refs"][0]["fetched"] is False
    assert local_row["detail_refs"][0]["fetched"] is True
    assert local_row["detail_refs"][0]["rows"] == 1
    assert local_row["detail_refs"][0]["locations"] == [{"line": 2}]
    assert any(i["code"] == "attachment_rejected" for i in parsed["issues"])

    # 无映射：全部阻塞，不得猜测检查键
    result = ri.classify(workspace, "expense", parsed["records"])
    assert result["blocked"] and not result["new"] and not result["changed"]
    assert all("QA 未映射" in item["reasons"][0] for item in result["blocked"])

    # 映射（助手生成、用户确认）后：前两条进入 new，第三条映射缺失 research_id 仍阻塞
    mapping = [{"question": "费用基础应用是否需要安装？", "check_order": "1", "research_id": "01",
                "scope": "示例医院/2026"},
               {"question": "费用政策依据是什么？", "check_order": "2", "research_id": "01",
                "scope": "示例医院/2026"}]
    applied, unmatched = ri.apply_mapping(parsed["records"], mapping)
    assert len(applied) == 2 and len(unmatched) == 1
    result = ri.classify(workspace, "expense", applied, default_scope="示例医院/2026")
    assert [item["research_id"] for item in result["new"]] == ["01", "01"]
    assert result["new"][0]["evidence_ref"].startswith("https://gitlab.example.com")
    assert "attachment.txt" in result["new"][1]["evidence_ref"]
    assert "line=2" in result["new"][1]["evidence_ref"]

    _write_all(workspace, result["new"])
    recorded = ri.record_import(workspace, "expense", qa.name, parsed["source_sha256"],
                                applied, default_scope="示例医院/2026")
    assert recorded["recorded"] and recorded["keys"] == 2

    # 同一 QA 再次导入（映射不变）：全部 unchanged，无新修订
    again = ri.classify(workspace, "expense", applied, default_scope="示例医院/2026")
    assert len(again["unchanged"]) == 2 and not again["new"] and not again["changed"]


def test_qa_text_blocks_parse(tmp_path):
    qa_md = tmp_path / "qa.md"
    qa_md.write_text("问：总账期间是否已启用？\n答：2026 年度期间已开启。\n"
                     "附件：https://gitlab.example.com/period.png\n\n"
                     "问：科目结构数量？\n答：共 3 套结构。\n", encoding="utf-8")
    parsed = rs.parse_source(qa_md, "general-ledger")
    assert parsed["coverage"]["source_subitems"] == 2
    first = parsed["records"][0]
    assert first["locator"]["kind"] == "qa" and first["check_order"] == ""
    assert first["suggested_evidence"] == "https://gitlab.example.com/period.png"
    assert parsed["records"][1]["conclusion"] == "共 3 套结构。"
    # 普通清单文本（无问答结构）不受 QA 模式影响
    plain = tmp_path / "plain.txt"
    plain.write_text("\n".join(V1) + "\n", encoding="utf-8")
    rows = rs.parse_source(plain, "expense")["records"]
    assert rows[0]["research_id"] == "01" and rows[0]["check_order"] == "1"
