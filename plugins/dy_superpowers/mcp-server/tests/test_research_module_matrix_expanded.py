"""虚构调研遍历真实矩阵，并组合验证范围、修订及巡检状态的完整链路。"""

import hashlib
import html
from functools import cache
import re
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

import server
from dy_superpowers_mcp.inspection import _matrix
from dy_superpowers_mcp.memory import module_memory_path


PLUGIN = Path(__file__).parents[2]
EXPECTED_COUNTS = {"expense": 92, "general-ledger": 82}


@cache
def matrix_source(module):
    """独立从标题和调研表首列取键；不把模板中的结论当测试业务事实。"""
    path = PLUGIN / f"skills/{module}-inspection/references/inspection-matrix.md"
    order = None
    pairs = []
    configuration = {}
    config_item = None
    config_row_next = False
    for line in path.read_text(encoding="utf-8").splitlines():
        heading = re.fullmatch(r"## 检查顺序 (.+)", line)
        if heading:
            order = heading.group(1)
            config_item = None
            config_row_next = False
        elif line.startswith("|") and "配置分类" in line and "配置项" in line:
            config_row_next = True
        elif config_row_next and line.startswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if not all(re.fullmatch(r"[-: ]*", cell) for cell in cells):
                config_item = html.unescape(cells[1])
                assert config_item, (module, order)
                config_row_next = False
        elif match := re.match(r"^\|\s*(\d+)｜", line):
            assert order is not None and config_item is not None
            pair = (order, match.group(1))
            pairs.append(pair)
            configuration[pair] = config_item
    assert len(pairs) == len(set(pairs)) == EXPECTED_COUNTS[module]
    revision, standards = _matrix(module)
    assert set(pairs) == set(standards)
    assert revision == hashlib.sha256(path.read_bytes()).hexdigest()
    return pairs, revision, standards, configuration


@pytest.fixture
def workspaces(tmp_path, monkeypatch):
    """两个独立业务工作区，拒绝任何意外网络请求。"""
    roots = (tmp_path / "项目甲", tmp_path / "项目乙")
    for root in roots:
        root.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))

    def reject_network(*args, **kwargs):
        pytest.fail("本测试只允许本地工具，不得访问 Odoo/GitLab")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    monkeypatch.setattr(socket, "create_connection", reject_network)
    return roots


def call(root, tool, **arguments):
    result, error = server.execute_tool(tool, {"trace_id": "expanded-matrix", **arguments}, root)
    assert error is None, (tool, error)
    return result


def payload(module, pair, scope="虚构甲公司/2026年/本期"):
    return dict(module=module, check_order=pair[0], research_id=pair[1],
                config_item=matrix_source(module)[3][pair], scope=scope,
                conclusion=f"虚构测试结论：{module}/{pair[0]}/{pair[1]}，仅测试存储契约。",
                evidence_ref="虚构测试输入，不是模板示例、生产配置或验收事实。",
                confirmed_by="虚构测试确认角色", confirmed_at="2026-01-01", confirmed=True)


def read(root, module, pair=None, **filters):
    if pair is not None:
        filters.update(check_order=pair[0], research_id=pair[1])
    return call(root, "dy_module_memory_read", module=module, **filters)


def inspection_report(module, pair, research, revision, standards):
    key = dict(module=module, check_order=pair[0], research_id=pair[1],
               scope_id=research["scope_id"], environment="虚构测试环境")
    return dict(expected_checks=[key], excluded_checks=[
        dict(key, check_order=order, research_id=rid, reason="本次仅验证虚构选定子项的状态门禁")
        for order, rid in standards if (order, rid) != pair
    ], records=[dict(key, record_id="虚构测试巡检记录", research_revision=research["revision"],
                     matrix_revision=revision, acceptance_standard=standards[pair],
                     evidence_refs=[dict(kind="manual", reference="虚构测试人工证据")],
                     verification_method="虚构测试，仅验证门禁，不声明生产验收", reason="虚构测试结论",
                     verified_at=datetime.now(timezone.utc).isoformat(), result="通过")])


@pytest.mark.parametrize("module", ["expense", "general-ledger"])
def test_every_matrix_key_roundtrips_with_pagination_and_workspace_isolation(workspaces, module):
    first, second = workspaces
    pairs, _, _, configuration = matrix_source(module)
    expected = {}
    for pair in pairs:
        data = payload(module, pair)
        saved = call(first, "dy_research_record_write", **data)
        record = read(first, module, pair, scope_id=saved["scope_id"])["records"][0]
        assert record["check_order"] == pair[0] and record["research_id"] == pair[1]
        assert record["config_item"] == configuration[pair]
        assert record["conclusion"] == data["conclusion"]
        assert record["scope"] == data["scope"]
        assert record["revision"] == saved["revision"] and record["status"] == "confirmed"
        expected[pair] = record

    paged = []
    for offset in range(0, len(pairs), 17):
        page = read(first, module, offset=offset, limit=17)
        assert page["offset"] == offset and page["limit"] == 17
        assert page["has_more"] == (offset + 17 < len(pairs))
        paged.extend(page["records"])
    assert len(paged) == EXPECTED_COUNTS[module]
    assert {(r["check_order"], r["research_id"]): r for r in paged} == expected
    assert read(first, module, offset=len(pairs), limit=17)["records"] == []
    assert read(second, module)["records"] == []

    other = "general-ledger" if module == "expense" else "expense"
    assert read(first, other)["records"] == []
    other_pair = matrix_source(other)[0][0]
    call(first, "dy_research_record_write", **payload(other, other_pair))
    assert len(read(first, module)["records"]) == len(pairs)
    assert len(read(first, other)["records"]) == 1

    independent = payload(module, pairs[0])
    independent["conclusion"] = "虚构乙工作区独立结论，不得泄露到甲工作区。"
    call(second, "dy_research_record_write", **independent)
    assert read(first, module, pairs[0])["records"][0] == expected[pairs[0]]
    assert read(second, module, pairs[0])["records"][0]["conclusion"] == independent["conclusion"]

    memory = module_memory_path(first, module)
    before = memory.read_bytes()
    for updates in [dict(check_order="NO-SUCH-CHECK"), dict(research_id="999"), dict(confirmed=False)]:
        _, error = server.execute_tool("dy_research_record_write",
                                      dict(trace_id="expanded-matrix", **dict(payload(module, pairs[0]), **updates)), first)
        assert error is not None and error["code"] == -32602
        assert memory.read_bytes() == before
    assert len(read(first, module)["records"]) == len(pairs)


@pytest.mark.parametrize("module", ["expense", "general-ledger"])
def test_multiscope_revision_retirement_and_inspection_require_current_confirmed_record(workspaces, module):
    root, other_workspace = workspaces
    pairs, matrix_revision, standards, _ = matrix_source(module)
    pair = pairs[0]
    first_data = dict(payload(module, pair), research_complete=True)
    first = call(root, "dy_research_record_write", **first_data)
    second_data = dict(payload(module, pair, scope="虚构乙公司/2026年/本期"), research_complete=True)
    second = call(root, "dy_research_record_write", **second_data)
    assert first["scope_id"] != second["scope_id"]
    _, ambiguous = server.execute_tool("dy_module_memory_read", dict(
        trace_id="expanded-matrix", module=module, check_order=pair[0], research_id=pair[1]), root)
    assert ambiguous is not None and ambiguous["code"] == -32602
    first_record = read(root, module, pair, scope_id=first["scope_id"])["records"][0]
    second_record = read(root, module, pair, scope_id=second["scope_id"])["records"][0]
    assert read(root, module, scope=second_data["scope"])["records"] == [second_record]
    page1 = read(root, module, limit=1)
    page2 = read(root, module, offset=1, limit=1)
    assert page1["has_more"] and not page2["has_more"]
    assert {page1["records"][0]["scope_id"], page2["records"][0]["scope_id"]} == {first["scope_id"], second["scope_id"]}

    before_report = inspection_report(module, pair, first_record, matrix_revision, standards)
    assert call(root, "dy_inspection_validate", report=before_report)["valid"]
    memory = module_memory_path(root, module)
    unchanged = memory.read_bytes()
    for updates in [dict(expected_revision="stale"), dict(status="pending"), dict(status="retired")]:
        args = dict(first_data, scope_id=first["scope_id"], expected_revision=first_record["revision"])
        args.update(updates)
        _, error = server.execute_tool("dy_research_record_write", dict(trace_id="expanded-matrix", **args), root)
        assert error is not None and error["code"] == -32602
        assert memory.read_bytes() == unchanged

    changed = call(root, "dy_research_record_write", **dict(
        first_data, scope_id=first["scope_id"], expected_revision=first_record["revision"],
        conclusion="虚构测试修订：原公司范围新增必需依赖，不涉及乙公司。"))
    current = read(root, module, pair, scope_id=changed["scope_id"])["records"][0]
    assert current["scope_id"] == first_record["scope_id"] and current["revision"] != first_record["revision"]
    assert len(current["history"]) == 1
    assert current["history"][0]["conclusion"] == first_data["conclusion"]
    assert current["history"][0]["status"] == "superseded"
    stale_validation = call(root, "dy_inspection_validate", report=before_report)
    assert not stale_validation["valid"]
    assert any("调研修订已变化" in e["message"] for e in stale_validation["errors"])
    refreshed_report = inspection_report(module, pair, current, matrix_revision, standards)
    assert call(root, "dy_inspection_validate", report=refreshed_report)["valid"]

    retired = call(root, "dy_research_record_write", **dict(
        first_data, conclusion=current["conclusion"], scope_id=current["scope_id"],
        expected_revision=current["revision"], status="retired", retirement_reason="虚构甲公司本期范围撤回"))
    stopped = read(root, module, pair, scope_id=retired["scope_id"])["records"][0]
    assert stopped["status"] == "retired" and len(stopped["history"]) == 2
    assert stopped["history"][-1]["conclusion"] == current["conclusion"]
    # 使用停用后的最新revision也不能绕过confirmed门禁。
    stopped_report = inspection_report(module, pair, stopped, matrix_revision, standards)
    stopped_validation = call(root, "dy_inspection_validate", report=stopped_report)
    assert not stopped_validation["valid"]
    assert any("范围已停用" in e["message"] for e in stopped_validation["errors"])
    assert read(root, module, pair, scope_id=second["scope_id"])["records"][0] == second_record
    other_scope_report = inspection_report(module, pair, second_record, matrix_revision, standards)
    assert call(root, "dy_inspection_validate", report=other_scope_report)["valid"]
    assert len(read(root, module)["records"]) == 2
    assert read(other_workspace, module)["records"] == []
