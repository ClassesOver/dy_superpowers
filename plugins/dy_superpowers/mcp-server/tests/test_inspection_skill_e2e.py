"""通过真实 stdio MCP 验证两份巡检技能的工具链；真实 Odoo 测试需显式启用。"""

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
from threading import Thread

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import MCPError

PLUGIN = Path(__file__).parents[2]
sys.path.insert(0, str(PLUGIN / "mcp-server/src"))
from dy_superpowers_mcp.qa_templates import load_template
from dy_superpowers_mcp.qa_store import read_qa
from template_qa_helpers import make_report

DOMAINS = {
    "expense": {
        "addon": "dy_expense",
        "model": "dy.expense.document.type",
        "probes": [
            ("dy_expense", "dy.expense.document.type"),
            ("dy_expense", "dy.expense.document.type.sequence"),
            ("dy_expense", "dy.expense.city.season"),
            ("dy_expense", "dy.expense.permission.configuration"),
            ("dy_expense", "dy.expense.requisition"),
            ("dy_expense", "dy.expense.payment.requisition"),
            ("dy_expense", "dy.expense.repayment.requisition"),
            ("dy_fin_expense_pf", "dy.fin.expense.pf"),
            ("dy_budget_control_expense", "dy.budget.control.rule.expense.item.line"),
            ("dy_hrfm_expense", "dy.hrfm.expense.execute.rule"),
        ],
    },
    "general-ledger": {
        "addon": "dy_fin_mdm",
        "model": "dy.fin.account.period",
        "probes": [
            ("dy_fin_mdm", "dy.fin.account.period"),
            ("dy_fin_mdm", "dy.fin.account.period.open.and.done"),
            ("dy_fin_mdm", "dy.fin.account.structure"),
            ("dy_fin_mdm", "dy.fin.account.analytic.plan"),
            ("dy_fin_mdm", "dy.fin.cash.flow.mapping"),
            ("dy_fin_acc", "account.move"),
            ("dy_fin_acc", "account.move.line"),
            ("dy_fin_acc", "dy.fin.acc.account.periodicity.voucher"),
            ("dy_fin_gaap", "account.mapping"),
            ("dy_fin_gaap", "budget.account.move"),
            ("dy_fin_pf", "dy.fin.pf.account.move.generation.rule"),
        ],
    },
}


def _parameters(workspace, connection):
    config = json.loads((PLUGIN / ".mcp.json").read_text())["mcpServers"]["dy-superpowers-odoo"]
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("ODOO_", "DY_ODOO_", "OTEL_", "DY_SUPERPOWERS_OTLP"))
           and key not in {"VIRTUAL_ENV", "DY_WORKSPACE_ROOT", "DY_BASIC_MEMORY_COMMAND"}}
    env.update(connection)
    env.update(CODEX_HOME=str(workspace / "codex"), DY_WORKSPACE_ROOT=str(workspace))
    return StdioServerParameters(
        command=str(PLUGIN / config["command"]),
        args=config["args"],
        cwd=str(workspace), env=env,
    )


def _report(module, environment, *, workspace, scope_id=None, result='未检查', reason='端到端测试未执行业务验收'):
    state = read_qa(workspace, module, scope_id) if scope_id else read_qa(workspace, module)
    report = make_report(workspace, module, state=state, environment=environment) if state['records'] else {
        'basis': dict(module=module, scope_id=scope_id or 'e2e-scope', environment=environment,
                      template_revision=state['template_revision'], answer_version='no-confirmed-answers'),
        'expected_checks': [dict(question_id=q['question_id']) for q in state['questions']],
        'records': []}
    if not state['records']:
        # 真实只读模式在没有确认QA时只报告阻塞，不伪造可发布依据。
        return report
    return report


def _save_summary(workspace, data):
    path = workspace / "e2e-summary.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


async def _workflow(workspace, module, connection, *, live=False):
    spec = DOMAINS[module]
    trace_id = f"e2e-{module}"
    summary = {"module": module, "mode": "real-odoo-readonly" if live else "mock-odoo",
               "skill": f"{module}-inspection", "calls": [], "gaps": []}
    async with stdio_client(_parameters(workspace, connection)) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            discovered = {item.name for item in (await session.list_tools()).tools}
            required = {"dy_session_initialize", "dy_knowledge_module_info", "dy_odoo_module_list_installed",
                        "dy_odoo_module_info", "dy_odoo_model_fields", "dy_odoo_record_search",
                        "dy_odoo_record_read", "dy_module_memory_read", "dy_research_answers_save",
                        "dy_inspection_validate", "dy_inspection_report_save", "dy_inspection_report_read",
                        "dy_trace_read"}
            assert required <= discovered

            async def call(tool, **arguments):
                summary["calls"].append(tool)
                response = await session.call_tool(tool, {"trace_id": trace_id, **arguments})
                assert not response.model_dump(by_alias=True).get("isError", False)
                text = response.content[0].text
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text

            initialized = await call("dy_session_initialize", source="startup")
            assert isinstance(initialized, str)
            assert not (workspace / ".dy_superpowers").exists()
            knowledge = await call("dy_knowledge_module_info", module=spec["addon"])
            assert knowledge["found"]
            summary["source_repo"] = knowledge.get("source_repo")

            modules, offsets = [], []
            for offset in range(0, 10000, 100):
                page = await call("dy_odoo_module_list_installed", offset=offset)
                assert isinstance(page, list) and len(page) <= 100
                modules.extend(page)
                offsets.append(offset)
                if len(page) < 100:
                    break
            else:
                pytest.fail("模块列表超过测试上限，不能报告已取全量")
            installed = {item["name"] for item in modules}
            summary.update(installed_module_count=len(modules), page_offsets=offsets,
                           relevant_modules=sorted(installed & {addon for addon, _ in spec["probes"]}))
            targeted = await call("dy_odoo_module_info", module=spec["addon"])
            summary["core_module_installed"] = any(item.get("state") == "installed" for item in targeted)

            if live:
                for addon, model in spec["probes"]:
                    if addon not in installed:
                        summary["gaps"].append({"model": model, "reason": "对应模块未安装，未查询模型"})
                        continue
                    try:
                        metadata = await call("dy_odoo_model_fields", model=model)
                        assert isinstance(metadata, dict)
                        probe = {"model": model, "field_count": len(metadata),
                                 "has_company_id": "company_id" in metadata, "has_state": "state" in metadata}
                        if model == "dy.expense.document.type":
                            assert {"company_ids", "is_active", "name", "document_type"} <= metadata.keys()
                            assert metadata["company_ids"]["type"] == "many2many"
                            probe["recipe_fields_verified"] = ["company_ids", "is_active", "name", "document_type"]
                        elif model == "dy.fin.account.period":
                            assert {"company_id", "date_from", "date_to", "state", "state_show", "cashier_period_state"} <= metadata.keys()
                            probe["recipe_fields_verified"] = ["company_id", "date_from", "date_to", "state", "state_show", "cashier_period_state"]
                        if model == "dy.fin.account.period.open.and.done":
                            probe["query"] = "临时操作向导：仅核对元数据，不读取向导行作为期间状态"
                        elif "id" not in metadata:
                            probe["query"] = "无 id 字段，不将抽象模型当作业务记录查询入口"
                            summary["gaps"].append({"model": model, "reason": probe["query"]})
                        else:
                            rows = await call("dy_odoo_record_search", model=model,
                                              args=[[]], kwargs={"fields": ["id"], "limit": 1})
                            probe["sample_count"] = len(rows)
                            if rows:
                                loaded = await call("dy_odoo_record_read", model=model,
                                                    ids=[rows[0]["id"]], fields=["id"])
                                assert loaded and loaded[0]["id"] == rows[0]["id"]
                        summary.setdefault("model_probes", []).append(probe)
                    except MCPError as exc:
                        summary["gaps"].append({"model": model, "reason": "远端读取失败或权限不足",
                                                "error_code": exc.code})
                report = _report(module, connection.get("DY_ODOO_ENVIRONMENT", connection["DB_NAME"]), workspace=workspace, result="待确认",
                                 reason="仅确认模型及最小字段可查询；公司范围、业务制度和岗位验收未确认")
                validation = await call("dy_inspection_validate", report=report)
                assert not validation["valid"] and validation["gate"] != "通过"
                summary["business_gate"] = validation["gate"]
                assert not (workspace / ".dy_superpowers/modules").exists()
                summary.update(protocol_ok=True, remote_writes=False)
                _save_summary(workspace, summary)
                return summary
            else:
                template = load_template(module)
                question = template['questions'][0]
                research = dict(module=module, template_revision=template['revision'], scope='隔离端到端测试夹具',
                    batch_id='e2e-first', answers=[dict(question_id=question['question_id'], answer='本期基础应用按清单配置',
                        research_complete=False, expected_revision=None)])
                with pytest.raises(MCPError) as denied:
                    await call('dy_research_answers_save', **research, confirmed=False)
                assert denied.value.code == -32602
                saved = await call('dy_research_answers_save', **research, confirmed=True)
                state = await call('dy_module_memory_read', module=module, scope_id=saved['scope_id'])
                record = state['records'][0]
                context = dict(skill=f'{module}-inspection', module=module, question_id=question['question_id'],
                    scope_id=state['scope_id'], template_revision=state['template_revision'],
                    answer_version=state['answer_version'], answer_revision=record['revision'], environment='e2e-db')
                await call("dy_odoo_module_info", module=spec["addon"], trace_context=context)
                metadata = await call("dy_odoo_model_fields", model=spec["model"], trace_context=context)
                assert "company_id" in metadata
                rows = await call("dy_odoo_record_search", model=spec["model"], args=[[["company_id", "=", 1]]],
                                  kwargs={"fields": ["id", "company_id"], "limit": 1}, trace_context=context)
                assert [row["id"] for row in rows] == [11]
                assert rows[0]["company_id"][0] == 1
                absent = await call("dy_odoo_record_search", model=spec["model"], args=[[["company_id", "=", 999]]],
                                    kwargs={"fields": ["id"], "limit": 1}, trace_context=context)
                assert absent == []
                await call("dy_odoo_record_read", model=spec["model"], ids=[rows[0]["id"]],
                           fields=["id"], trace_context=context)
                report = _report(module, 'e2e-db', workspace=workspace, scope_id=saved['scope_id'])
                prepared = await call('dy_inspection_prepare', module=module, scope_id=saved['scope_id'])
                report['basis'] = prepared['basis']
                validation = await call('dy_inspection_validate', report=report)
                assert validation['valid'] and validation['gate'] == '待确认'
                missing = deepcopy(report); missing['records'].pop()
                assert not (await call('dy_inspection_validate', report=missing))['valid']
                forged = deepcopy(report); forged['basis']['environment'] = 'PROD'
                assert not (await call('dy_inspection_validate', report=forged))['valid']
                scoped = deepcopy(report); scoped['expected_checks'] = scoped['expected_checks'][:1]; scoped['records'] = scoped['records'][:1]
                assert not (await call('dy_inspection_validate', report=scoped))['valid']
                summary['business_gate'] = validation['gate']

            with pytest.raises(MCPError) as denied:
                await call("dy_inspection_report_save", confirmed=False, report=report)
            assert denied.value.code == -32602
            artifact = await call("dy_inspection_report_save", confirmed=True, report=report)
            loaded = await call("dy_inspection_report_read", module=module, report_id=artifact["report_id"])
            path = (workspace / artifact["path"]).resolve()
            assert path.is_relative_to((workspace / ".dy_superpowers/reports").resolve())
            saved_content = path.read_text(encoding="utf-8")
            assert saved_content.split('\n## 机器可读数据\n', 1)[0].rstrip() + '\n' == loaded
            assert "待确认" in loaded and "机器可读数据" not in loaded
            summary.update(report_path=artifact["path"], expected_count=len(report["expected_checks"]))
            second_artifact = await call("dy_inspection_report_save", confirmed=True, report=report)
            assert second_artifact["report_id"] != artifact["report_id"]
            assert path.read_text(encoding="utf-8") == saved_content

            if not live:
                updated = {**research, 'scope_id': saved['scope_id'], 'batch_id': 'e2e-second',
                    'answers': [{**research['answers'][0], 'answer': '修订后的配置清单', 'expected_revision': record['revision']}]}
                await call('dy_research_answers_save', **updated, confirmed=True)
                stale = await call('dy_inspection_validate', report=report)
                assert not stale['valid']
                with pytest.raises(MCPError):
                    await call('dy_inspection_report_save', confirmed=True, report=report)
            events = await call("dy_trace_read")
            assert events and any(event.get("event") == "inspection.report.save" for event in events)
            serialized = json.dumps(events, ensure_ascii=False)
            secret = connection.get("APP_PASSWORD", "")
            assert not secret or secret not in serialized
            assert "e2e-remote-row-must-not-log" not in serialized
            summary.update(trace_event_count=len(events), protocol_ok=True, remote_writes=False)
    _save_summary(workspace, summary)
    return summary


@pytest.mark.parametrize("module", DOMAINS)
def test_inspection_skill_e2e_mock(tmp_path, module):
    calls = []
    sample_rows = [{"id": 11, "company_id": [1, "e2e-remote-row-must-not-log"]},
                   {"id": 12, "company_id": [2, "e2e-other-company"]}]
    installed = [{"id": index, "name": f"e2e_addon_{index}", "state": "installed"}
                 for index in range(1, 101)]
    installed.extend({"id": index, "name": addon, "state": "installed"}
                     for index, addon in enumerate(["dy_expense", "dy_fin_mdm"], 101))

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            params = request["params"]
            calls.append(params)
            if params["service"] == "common":
                assert params["method"] == "login"
                result = 7
            else:
                assert params["service"] == "object" and params["method"] == "execute_kw"
                _, _, _, model, method, args, kwargs = params["args"]
                assert method in {"search_read", "read", "fields_get"}
                if method == "fields_get":
                    result = {"id": {"type": "integer"}, "company_id": {"type": "many2one", "relation": "res.company"}}
                elif method == 'read':
                    assert len(args) == 1 and isinstance(args[0], list)
                    result = [{key: row[key] for key in set(kwargs['fields']) | {'id'}}
                              for row in sample_rows if row['id'] in args[0]]
                elif model == "ir.module.module":
                    rows = installed
                    for term in args[0]:
                        if isinstance(term, list) and term[:2] == ["name", "="]:
                            rows = [row for row in rows if row["name"] == term[2]]
                    offset = kwargs.get("offset", 0)
                    result = rows[offset:offset + kwargs.get("limit", 100)]
                else:
                    selected = sample_rows
                    for field, operator, value in args[0]:
                        assert operator in {"=", "in"}, "mock 只实现测试使用的 domain 运算符"
                        def matches(row):
                            actual = row[field][0] if isinstance(row[field], list) else row[field]
                            return actual == value if operator == "=" else actual in value
                        selected = [row for row in selected if matches(row)]
                    offset = kwargs.get("offset", 0)
                    result = [{key: row[key] for key in kwargs["fields"]}
                              for row in selected[offset:offset + kwargs.get("limit", 100)]]
            body = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    rpc = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=rpc.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{rpc.server_port}"
    workspace = tmp_path / module
    workspace.mkdir()
    try:
        summary = asyncio.run(asyncio.wait_for(_workflow(workspace, module, {
            "APP_URL": origin, "APP_URL": origin,
            "DB_NAME": "e2e-db", "APP_USER": "e2e-user", "APP_PASSWORD": "e2e-fixture-value",
        }), timeout=180))
    finally:
        rpc.shutdown()
        rpc.server_close()
        thread.join(timeout=2)
    assert summary["page_offsets"] == [0, 100]
    assert summary["installed_module_count"] == 102
    assert any(call.get("service") == "object" for call in calls)


@pytest.mark.skipif(not os.environ.get("DY_E2E_ODOO_ENV_FILE"), reason="需显式设置 DY_E2E_ODOO_ENV_FILE")
@pytest.mark.parametrize("module", DOMAINS)
def test_inspection_skill_e2e_live_readonly(tmp_path, module):
    from dotenv import dotenv_values

    values = dotenv_values(Path(os.environ["DY_E2E_ODOO_ENV_FILE"]).resolve())
    keys = ("APP_URL", "APP_URL", "DB_NAME", "APP_USER", "APP_PASSWORD")
    if any(not isinstance(values.get(key), str) or not values[key] for key in keys):
        pytest.fail("真实端测连接配置缺失；不会输出配置值")
    workspace = tmp_path / f"live-{module}"
    workspace.mkdir()
    summary = asyncio.run(asyncio.wait_for(_workflow(
        workspace, module, {key: values[key] for key in keys}, live=True,
    ), timeout=480))
    assert summary["protocol_ok"]
    assert summary["business_gate"] == "待确认"
    assert summary["remote_writes"] is False
