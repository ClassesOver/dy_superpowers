"""调研修订、异常隔离和范围生命周期的回归用例。"""

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from dy_superpowers_mcp.memory import module_memory_path
from dy_superpowers_mcp.research import read_record, write_record, _legacy_visible


def data(**updates):
    return {"module": "test-expense", "check_order": "EXP-1-001", "research_id": "01",
            "config_item": "应用安装", "scope": "公司A/本期", "conclusion": "本期上线",
            "confirmed": True, "confirmed_by": "顾问", "confirmed_at": "2026-10-21", **updates}


def read(root, **kwargs):
    return read_record(root, "test-expense", "EXP-1-001", "01", **kwargs)


def corrupt(root):
    path = module_memory_path(root, "test-expense")
    current = read(root)
    current.pop("revision")
    text = path.read_text()
    start = text.index("<!-- dy-research-record:start -->")
    end = text.index("<!-- dy-research-record:end -->") + len("<!-- dy-research-record:end -->")
    text = text[:start] + "\n".join(_legacy_visible(current)) + text[end:]
    path.write_text(text.replace("- 调研结论：本期上线", "- 调研结论：手动改动", 1))


def test_corrupt_record_does_not_block_unrelated_read_or_write(tmp_path):
    write_record(tmp_path, data())
    write_record(tmp_path, data(check_order="EXP-1-002"))
    corrupt(tmp_path)
    normal = read_record(tmp_path, "test-expense", "EXP-1-002", "01")
    write_record(tmp_path, data(check_order="EXP-1-002", conclusion="独立修订", expected_revision=normal["revision"]))
    assert read_record(tmp_path, "test-expense", "EXP-1-002", "01")["conclusion"] == "独立修订"
    with pytest.raises(ValueError, match="不一致"):
        read(tmp_path)


def test_repair_requires_confirmation_and_current_block_revision(tmp_path):
    write_record(tmp_path, data())
    corrupt(tmp_path)
    with pytest.raises(ValueError, match="不一致") as failure:
        read(tmp_path)
    revision = failure.value.revision
    before = module_memory_path(tmp_path, "test-expense").read_text()
    with pytest.raises(PermissionError):
        write_record(tmp_path, data(repair=True, expected_revision=revision, confirmed=False))
    with pytest.raises(ValueError, match="冲突"):
        write_record(tmp_path, data(repair=True, expected_revision="old"))
    assert module_memory_path(tmp_path, "test-expense").read_text() == before
    write_record(tmp_path, data(repair=True, expected_revision=revision, conclusion="重新核实结论"))
    current = read(tmp_path)
    assert current["conclusion"] == "重新核实结论"
    assert current["history"][0]["status"] == "invalidated"
    assert "手动改动" in current["history"][0]["original_block"]


def test_invalid_json_with_known_heading_is_not_treated_as_missing(tmp_path):
    write_record(tmp_path, data())
    path = module_memory_path(tmp_path, "test-expense")
    current = read(tmp_path)
    current.pop("revision")
    path.write_text("\n".join(_legacy_visible(current)))
    path.write_text(re.sub(r"<!-- dy-research-record:data .*? -->", "<!-- dy-research-record:data {bad} -->", path.read_text()))
    with pytest.raises(ValueError) as failure:
        read(tmp_path)
    write_record(tmp_path, data(repair=True, expected_revision=failure.value.revision))
    assert read(tmp_path)["status"] == "confirmed"


def test_stale_research_revision_cannot_replace_newer_conclusion(tmp_path):
    write_record(tmp_path, data())
    first = read(tmp_path)
    write_record(tmp_path, data(conclusion="顾问B修订", expected_revision=first["revision"]))
    with pytest.raises(ValueError, match="冲突"):
        write_record(tmp_path, data(conclusion="顾问A过期修订", expected_revision=first["revision"]))
    with pytest.raises(ValueError, match="expected_revision"):
        write_record(tmp_path, data(conclusion="无版本覆盖"))
    assert read(tmp_path)["conclusion"] == "顾问B修订"


def test_future_confirmation_date_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="不能晚于当前日期"):
        write_record(tmp_path, data(confirmed_at="2999-01-01"))


def test_only_one_concurrent_revision_commits(tmp_path):
    write_record(tmp_path, data())
    revision = read(tmp_path)["revision"]

    def update(index):
        try:
            write_record(tmp_path, data(conclusion=f"结论{index}", expected_revision=revision))
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(update, range(4))) == 1
    assert len(read(tmp_path)["history"]) == 1


def test_scope_normalization_and_stable_id_prevent_cosmetic_duplicates(tmp_path):
    write_record(tmp_path, data())
    first = read(tmp_path)
    with pytest.raises(ValueError, match="expected_revision"):
        write_record(tmp_path, data(scope="公司A / 本期"))
    write_record(tmp_path, data(scope="公司A / 本期", expected_revision=first["revision"]))
    current = read(tmp_path)
    assert current["scope_id"] == first["scope_id"]
    write_record(tmp_path, data(scope="公司A/第二阶段", scope_id=current["scope_id"], expected_revision=current["revision"]))
    assert read(tmp_path, scope_id=first["scope_id"])["scope"] == "公司A/第二阶段"


def test_retirement_keeps_history_and_requires_reason(tmp_path):
    write_record(tmp_path, data())
    first = read(tmp_path)
    with pytest.raises(ValueError, match="停用原因"):
        write_record(tmp_path, data(status="retired", expected_revision=first["revision"]))
    write_record(tmp_path, data(status="retired", retirement_reason="本期范围撤销", expected_revision=first["revision"]))
    retired = read(tmp_path)
    assert retired["status"] == "retired"
    assert retired["retirement_reason"] == "本期范围撤销"
    assert retired["history"][0]["status"] == "superseded"
    assert retired["revision"] != first["revision"]


def test_legacy_record_gets_stable_scope_id_without_rewriting_on_read(tmp_path):
    write_record(tmp_path, data())
    path = tmp_path / ".dy_superpowers/modules/test-expense/research.md"
    current = read(tmp_path)
    current.pop("revision")
    path.write_text("\n".join(_legacy_visible(current)))
    text = path.read_text()
    payload = json.loads(re.search(r"<!-- dy-research-record:data (.*) -->", text).group(1))
    payload.pop("scope_id")
    text = re.sub(r"^- 范围标识：.*\n", "", text, flags=re.MULTILINE)
    text = re.sub(r"<!-- dy-research-record:data .* -->", lambda _: '<!-- dy-research-record:data ' + json.dumps(payload, ensure_ascii=False, separators=(',', ':')) + ' -->', text)
    path.write_text(text)
    legacy = read(tmp_path)
    assert path.read_text() == text
    write_record(tmp_path, data(scope_id=legacy['scope_id'], expected_revision=legacy['revision']))
    assert read(tmp_path)['scope_id'] == legacy['scope_id']


def test_scope_label_cannot_be_reused_with_another_id(tmp_path):
    write_record(tmp_path, data(scope_id='company-a'))
    with pytest.raises(ValueError, match='范围'):
        write_record(tmp_path, data(scope_id='another-id'))


@pytest.mark.parametrize('updates', [{'scope_id': []}, {'status': []}, {'evidence_ref': {}}, {'expected_revision': []}])
def test_invalid_lifecycle_arguments_have_actionable_errors(tmp_path, updates):
    with pytest.raises(ValueError):
        write_record(tmp_path, data(**updates))
