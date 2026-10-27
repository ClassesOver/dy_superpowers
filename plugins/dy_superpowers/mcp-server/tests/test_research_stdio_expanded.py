"""真实 stdio 跨会话验证调研、主题、Wiki、报告与工作区隔离。"""
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import MCPError

PLUGIN = Path(__file__).parents[2]
sys.path.insert(0, str(PLUGIN / "mcp-server/src"))
from dy_superpowers_mcp.inspection import _matrix
from dy_superpowers_mcp.project_wiki import load_staged
from dy_superpowers_mcp.trace import TraceStore


def _parameters(workspace, codex_home):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("APP_", "DB_", "ODOO_", "DY_", "OTEL_"))
           and key not in {"VIRTUAL_ENV", "CODEX_HOME"}}
    env.update(CODEX_HOME=str(codex_home), DY_WORKSPACE_ROOT=str(workspace))
    return StdioServerParameters(
        command=sys.executable, args=[str(PLUGIN / "mcp-server/src/server.py"),
                                      "--workspace", str(workspace)],
        cwd=str(workspace), env=env)


def _uninspected_report(module, scope_id):
    revision, standards = _matrix(module)
    expected, records = [], []
    for index, ((order, item), standard) in enumerate(sorted(standards.items())):
        key = dict(module=module, check_order=order, research_id=item,
                   scope_id=scope_id, environment="隔离测试环境")
        expected.append(key)
        records.append({**key, "record_id": f"expanded-{index}",
                        "research_revision": "", "matrix_revision": revision,
                        "acceptance_standard": standard, "result": "未检查",
                        "reason": "仅测试交付报告链路，未访问业务系统",
                        "verification_method": "未执行业务验收，不得视为通过",
                        "verified_at": datetime.now(timezone.utc).isoformat(),
                        "evidence_refs": []})
    return {"expected_checks": expected, "excluded_checks": [], "records": records}


@pytest.mark.parametrize("module", ["expense", "general-ledger"])
def test_stdio_research_resume_report_and_workspace_isolation(tmp_path, module):
    workspace = tmp_path / "project-a"
    other = tmp_path / "project-b"
    workspace.mkdir()
    other.mkdir()
    codex_home = tmp_path / "codex"
    trace_id = "expanded-project-flow"
    order, item = sorted(_matrix(module)[1])[0]
    payload = dict(module=module, check_order=order, research_id=item,
                   config_item="仅测试：基础应用", scope="虚构甲医院/本期",
                   conclusion="虚构甲医院本期交付基础应用，尚未核验系统。",
                   evidence_ref="模拟多轮QA#第2轮；脱敏附件.xlsx#清单!A2:C3",
                   confirmed_by="模拟业务确认人", confirmed_at="2026-10-27",
                   confirmed=True)
    context = dict(skill="module-research", module=module, check_order=order, research_id=item)
    todo = f"# 调研待办\n\n- {module}/{order}/{item}：附件缺口是什么？\n"
    saved = {}

    async def call(session, name, arguments):
        response = await session.call_tool(name, {"trace_id": trace_id, **arguments})
        assert not response.model_dump(by_alias=True).get("isError", False), response
        content = response.content[0].text
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return content

    async def first_session():
        async with stdio_client(_parameters(workspace, codex_home)) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                await call(session, "dy_session_initialize", {"source": "startup"})
                for invalid in ({**payload, "confirmed": False},
                                {**payload, "attachment_refs": ["不可静默丢弃.xlsx"]}):
                    with pytest.raises(MCPError) as denied:
                        await call(session, "dy_research_record_write", invalid)
                    assert denied.value.code == -32602
                empty = await call(session, "dy_module_memory_read", {"module": module})
                assert empty["records"] == []
                first = await call(session, "dy_research_record_write",
                                   {**payload, "trace_context": context})
                saved.update(first)
                await call(session, "dy_research_record_write",
                           {**payload, "scope": "虚构乙医院/本期",
                            "conclusion": "乙医院独立范围结论，不复用甲医院结论。"})
                await call(session, "dy_memory_write", {"name": "delivery-register.md",
                           "content": todo, "expected_content": "", "confirmed": True})
                topic = dict(slug="research/confirmed-policy", title="已确认模块政策",
                             body="这是单独确认的模块政策，不能替代巡检验收。", module=module,
                             research_topic=True, confirmed=True)
                await call(session, "dy_notes_write", topic)
                original = await call(session, "dy_notes_read", {"slug": topic["slug"]})
                with pytest.raises(MCPError):
                    await call(session, "dy_notes_write", {**topic, "body": "过期覆盖",
                               "expected_content": "旧版本"})
                assert await call(session, "dy_notes_read", {"slug": topic["slug"]}) == original
                await call(session, "dy_notes_write", dict(slug="private", title="普通内部笔记",
                           body="普通笔记不得发布的唯一标记", confirmed=True))
                await call(session, "dy_project_configure", dict(name="虚构交付扩展测试",
                           code="expanded-project", confirmed=True))
                built = await call(session, "dy_wiki_build", {"confirmed": True})
                assert built["pages"] == 4 and not built["published"]
                pages = "\n".join(load_staged(workspace)[1].values())
                assert f"- 检查编号：{order}" in pages
                assert "普通笔记不得发布的唯一标记" not in pages
                assert "附件缺口是什么" not in pages
                updated = await call(session, "dy_research_record_write", {**payload,
                           "scope_id": first["scope_id"], "expected_revision": first["revision"],
                           "conclusion": "甲医院修订后结论：包含必需依赖，尚未验收。"})
                saved.update(updated)
                with pytest.raises(ValueError):
                    load_staged(workspace)
                with pytest.raises(MCPError) as conflict:
                    await call(session, "dy_research_record_write", {**payload,
                               "scope_id": first["scope_id"], "expected_revision": first["revision"]})
                assert conflict.value.code == -32602
                await call(session, "dy_wiki_build", {"confirmed": True})
                report = _uninspected_report(module, first["scope_id"])
                validated = await call(session, "dy_inspection_validate", {"report": report})
                assert validated["valid"] and validated["gate"] == "待确认"
                for _ in range(2):
                    result = await call(session, "dy_inspection_report_save",
                                        {"report": report, "confirmed": True})
                    saved.setdefault("reports", []).append(result)
                    assert result["gate"] == "待确认"
                    assert (workspace / result["pdf_path"]).read_bytes().startswith(b"%PDF-")
                assert saved["reports"][0]["report_id"] != saved["reports"][1]["report_id"]

    async def resumed_session():
        async with stdio_client(_parameters(workspace, codex_home)) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                initialized = await call(session, "dy_session_initialize", {"source": "resume"})
                assert "delivery-register.md" in initialized
                assert "甲医院修订后结论" not in initialized
                records = await call(session, "dy_module_memory_read", {"module": module})
                assert len(records["records"]) == 2 and not records["has_more"]
                current = next(row for row in records["records"] if row["scope_id"] == saved["scope_id"])
                assert current["revision"] == saved["revision"]
                assert current["history"][0]["conclusion"] == payload["conclusion"]
                assert await call(session, "dy_memory_read", {"name": "delivery-register.md"}) == todo
                for report in saved["reports"]:
                    content = await call(session, "dy_inspection_report_read",
                                         {"module": module, "report_id": report["report_id"]})
                    assert "完整检查数据" in content and "未检查" in content

    async def separate_project():
        async with stdio_client(_parameters(other, codex_home)) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                records = await call(session, "dy_module_memory_read", {"module": module})
                assert records["records"] == []
                notes = await call(session, "dy_notes_list", {})
                assert notes["notes"] == []
                with pytest.raises(MCPError):
                    await call(session, "dy_wiki_build", {"confirmed": True})
                with pytest.raises(MCPError):
                    await call(session, "dy_inspection_report_read",
                               {"module": module, "report_id": saved["reports"][0]["report_id"]})

    async def exercise():
        await first_session()
        await resumed_session()
        await separate_project()

    asyncio.run(asyncio.wait_for(exercise(), timeout=120))
    prior = os.environ.get("CODEX_HOME")
    try:
        os.environ["CODEX_HOME"] = str(codex_home)
        project_events = TraceStore(workspace).read(trace_id)
        other_events = TraceStore(other).read(trace_id)
        assert TraceStore(workspace).workspace_id != TraceStore(other).workspace_id
        assert any(event.get("event") == "research.write" for event in project_events)
        assert all(event.get("event") != "research.write" for event in other_events)
        assert "甲医院修订后结论" not in json.dumps(other_events, ensure_ascii=False)
    finally:
        if prior is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = prior
