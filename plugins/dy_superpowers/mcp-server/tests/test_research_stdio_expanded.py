"""真实 stdio 跨会话验证调研、报告与工作区隔离。"""
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
from dy_superpowers_mcp.qa_templates import load_template
from dy_superpowers_mcp.qa_store import read_qa
from template_qa_helpers import make_report


def _parameters(workspace, codex_home):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("APP_", "DB_", "ODOO_", "DY_", "OTEL_"))
           and key not in {"VIRTUAL_ENV", "CODEX_HOME"}}
    env.update(CODEX_HOME=str(codex_home), DY_WORKSPACE_ROOT=str(workspace))
    return StdioServerParameters(
        command=sys.executable, args=[str(PLUGIN / "mcp-server/src/server.py"),
                                      "--workspace", str(workspace)],
        cwd=str(workspace), env=env)


def _uninspected_report(workspace, module, scope_id):
    state = read_qa(workspace, module, scope_id)
    return make_report(workspace, module, state=state, environment='UAT')


@pytest.mark.parametrize("module", ["expense", "general-ledger"])
def test_stdio_scope_optional_routing_and_generic_export(tmp_path, module):
    from openpyxl import load_workbook

    workspace = tmp_path / "project"
    workspace.mkdir()
    total = len(load_template(module)['questions'])
    item = load_template(module)["questions"][0]

    async def exercise():
        async with stdio_client(_parameters(workspace, tmp_path / "codex")) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tool_map = {tool.name: tool for tool in (await session.list_tools()).tools}
                progress_schema = tool_map["dy_research_progress"].model_dump(by_alias=True)["inputSchema"]
                assert "intent" not in progress_schema["required"]
                assert progress_schema["properties"]["intent"]["enum"] == ["research", "progress", "export", "import", "confirm"]

                async def call(name, **arguments):
                    response = await session.call_tool(name, dict(trace_id="optional-scope", module=module, **arguments))
                    return json.loads(response.content[0].text)

                empty = await call("dy_research_progress")
                assert (empty["answered"], empty["total"], empty["route"]) == (0, total, "excel")
                assert empty["scope_mode"] == "unscoped"
                assert not (workspace / ".dy_superpowers").exists()
                with pytest.raises(MCPError, match="明确确认"):
                    await call("dy_research_unanswered_export", confirmed=False)
                payload = dict(template_revision=load_template(module)['revision'],
                               answers=[dict(question_id=item['question_id'], answer='仅测试：业务已确认',
                                             research_complete=True, expected_revision=None)], confirmed=True)
                first = await call('dy_research_answers_save', **payload, batch_id='first', scope='虚构甲院/本期')
                single = await call('dy_research_progress')
                assert single['scope_mode'] == 'selected' and single['answered'] == 1
                assert single['scope_id'] == first['scope_id']
                await call('dy_research_answers_save', **payload, batch_id='second', scope='虚构乙院/本期')
                multiple = await call('dy_research_progress')
                assert multiple['scope_mode'] == 'multiple' and multiple['answered'] == 0
                assert len(multiple['scope_progress']) == 2
                exported = await call('dy_research_unanswered_export', confirmed=True)
                assert len(exported['files']) == 2
                assert all(f['question_count'] == total for f in exported['files'])
                for f in exported['files']:
                    assert Path(f['path']).is_file()

    asyncio.run(asyncio.wait_for(exercise(), timeout=30))


@pytest.mark.parametrize("module", ["expense", "general-ledger"])
def test_stdio_partial_save_with_unknown_scope_resumes_without_completing_research(tmp_path, module):
    """范围未补齐不阻塞局部落盘，也不能增加完整存储度或放行巡检。"""
    workspace = tmp_path / "project"
    workspace.mkdir()
    codex_home = tmp_path / "codex"
    trace_id = "partial-save"
    scope = "范围待补充；确认批次：partial-save/1"
    item = load_template(module)["questions"][0]
    saved = {}
    payload = dict(module=module, template_revision=load_template(module)['revision'], scope=scope,
        batch_id='partial-first', answers=[dict(question_id=item['question_id'], answer='仅测试：局部确认',
        research_complete=False, expected_revision=None)])

    async def call(session, name, **arguments):
        response = await session.call_tool(name, {"trace_id": trace_id, **arguments})
        assert not response.model_dump(by_alias=True).get("isError", False), response
        return json.loads(response.content[0].text)

    async def first_session():
        async with stdio_client(_parameters(workspace, codex_home)) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                with pytest.raises(MCPError, match="明确确认"):
                    await call(session, "dy_research_answers_save", **payload, confirmed=False)
                assert not (workspace / ".dy_superpowers/modules").exists()
                saved.update(await call(session, "dy_research_answers_save", **payload, confirmed=True))
                read = await call(session, "dy_module_memory_read", module=module,
                                  question_ids=[item["question_id"]],
                                  scope_id=saved["scope_id"])
                assert read["records"][0]["answer"] == payload["answers"][0]["answer"]
                assert read["scope"] == saved["scope"]
                assert read["records"][0]["research_complete"] is False
                progress = await call(session, "dy_research_progress", module=module,
                                      scope_id=saved["scope_id"])
                assert progress["answered"] == 0 and progress["route"] == "excel"
                assert progress["confirmed_count"] == 1 and progress["partial_count"] == 1
                assert progress["unknown_count"] == 0
                assert progress["unanswered_count"] == progress["total"] - 1
                assert f"1/{progress['total']}" in progress["progress_summary"]

                report = _uninspected_report(workspace, module, saved["scope_id"])
                report['records'][0]['result'] = '通过'
                checked = await call(session, 'dy_inspection_validate', report=report)
                assert checked['valid'] is False
                assert any('完整' in error['message'] for error in checked['errors'])

    async def resumed_session():
        async with stdio_client(_parameters(workspace, codex_home)) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                read = await call(session, "dy_module_memory_read", module=module,
                                  question_ids=[item["question_id"]],
                                  scope_id=saved["scope_id"])
                current = read["records"][0]
                assert current["revision"] == saved["records"][0]["revision"]
                assert current["research_complete"] is False
                other = await call(session, 'dy_research_answers_save', **{
                    **payload, 'scope': '另一个确认批次', 'batch_id': 'partial-second', 'confirmed': True})
                assert other['scope_id'] != saved['scope_id']
                final = await call(session, 'dy_module_memory_read', module=module)
                assert final['scope_mode'] == 'multiple' and final['records'] == []
                assert all(s['partial_count'] == 1 for s in final['scope_progress'])

    async def exercise():
        await first_session()
        await resumed_session()

    asyncio.run(asyncio.wait_for(exercise(), timeout=30))
