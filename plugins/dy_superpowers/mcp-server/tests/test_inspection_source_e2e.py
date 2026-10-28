"""真实调研候选输入驱动的巡检测试；来源解析与 module-research 不在本文件范围内。

DY_E2E_RESEARCH_SOURCES 指向本地候选 JSON 目录，DY_E2E_ODOO_ENV_FILE 指向连接配置。
默认只运行纯内存计划测试，真实数据不进入版本库、不自动写 confirmed 调研。
"""
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

import pytest
from mcp import ClientSession
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import MCPError

from test_inspection_skill_e2e import _matrix, _parameters, _report

# 仅列可只读核验的配置，不把岗位、支付、核销、关账等操作伪装成读取验证。
RECIPES = {
    "expense": [
        {"order": "2", "model": "res.company", "domain": [], "fields": ["id"],
         "expected": r"(\d+)个组织记录"},
        {"order": "6", "model": "dy.expense.document.type", "domain": [["is_active", "=", True]],
         "fields": ["id", "company_ids", "is_active"], "expected": r"(\d+)个单据类型"},
        {"order": "10", "model": "dy.expense.application.item", "domain": [["is_active", "=", True]],
         "fields": ["id", "company_ids", "is_active"], "expected": r"(\d+)个有效费用申请项目"},
        {"order": "11", "model": "dy.expense.item", "domain": [["is_active", "=", True]],
         "fields": ["id", "company_ids", "is_active"], "expected": r"(\d+)个有效费用项目"},
        {"order": "13", "model": "dy.expense.city.season", "domain": [["active", "=", True]],
         "fields": ["id", "low_season_ids", "peak_season_ids"], "expected": r"(\d+)个有效城市配置"},
        {"order": "14", "model": "dy.expense.policy", "domain": [["is_active", "=", True]], "policy_head": True,
         "fields": ["id", "company_ids", "line_ids"], "expected": r"(\d+)个.*?政策头"},
        {"order": "14", "model": "dy.expense.policy.line", "domain": [], "policy_scope": True,
         "fields": ["id", "expense_policy", "effective_date", "expiry_date", "start_date", "end_date", "control_level"]},
        {"order": "8", "model": "dy.budget.control.execution.rule",
         "domain": [["source_doc", "in", ["expense_report", "expense_payment", "expense_requisition"]]],
         "fields": ["id", "company_id", "source_doc", "date_from", "date_to", "is_active_flag"]},
        {"order": "9", "model": "dy.hrfm.expense.execute.rule",
         "domain": [["source_doc", "in", ["expense_report", "expense_payment", "expense_requisition"]]],
         "fields": ["id", "company_id", "source_doc", "date_from", "date_to", "active_flag", "execute_node"]},
        {"order": "23", "model": "dy.expense.permission.configuration", "domain": [],
         "fields": ["id", "company_id", "effective_date", "expiration_date", "is_active"]},
        {"order": "15", "model": "dy.fin.account.period",
         "domain": [["date_from", "<=", "{as_of}"], ["date_to", ">=", "{as_of}"]],
         "fields": ["id", "company_id", "state", "expense_period_status"]},
    ],
    "general-ledger": [
        {"order": "GL-1-002", "model": "res.company", "domain": [], "fields": ["id"], "expected": r"配置(\d+)个公司"},
        {"order": "GL-2-005", "model": "dy.fin.account.structure", "domain": [],
         "fields": ["id"], "expected": r"配置(\d+)套结构"},
        {"order": "GL-2-006", "model": "dy.fin.account.type", "domain": [], "fields": ["id"], "expected": r"配置(\d+)种科目类型"},
        {"order": "GL-2-010", "model": "dy.account.permissions.settings", "domain": [["active", "=", True]],
         "fields": ["id", "permission_user_ids", "permission_account_ids"], "expected": r"有(\d+)套启用"},
        {"order": "GL-3-012", "model": "dy.fin.account.period", "domain": [],
         "fields": ["id", "company_id", "state", "date_from", "date_to"], "expected": r"(\d+)个期间记录"},
        {"order": "GL-5-016", "model": "dy.fin.account.analytic.plan", "domain": [],
         "fields": ["id", "company_id", "plan_ids"], "expected": r"已建(\d+)套方案"},
        {"order": "GL-6-019", "model": "account.journal", "domain": [["active", "=", True]],
         "fields": ["id", "company_id", "account_structure_id", "journal_company_ids"], "expected": r"(\d+)个有效分类账"},
        {"order": "GL-6-021", "model": "dy.fin.acc.account.periodicity.voucher", "domain": [],
         "fields": ["id", "company_id", "line_ids"], "expected": r"配置(\d+)个经常性凭证模板"},
        {"order": "GL-8-024", "model": "dy.fin.acc.financial.report.item", "domain": [],
         "fields": ["id"], "expected": r"配置(\d+)条财务报表项目"},
        {"order": "GL-8-027", "model": "dy.fin.account.cash.flow.statement.entry", "domain": [],
         "fields": ["id", "account_structure_id"], "expected": r"配置(\d+)条现金流量表项"},
        {"order": "GL-8-028", "model": "dy.fin.cash.flow.mapping", "domain": [],
         "fields": ["id", "company_id", "active"], "expected": r"配置(\d+)条现金流映射"},
        {"order": "GL-9-029", "model": "account.mapping", "domain": [],
         "fields": ["id", "company_id"], "expected": r"配置(\d+)条财务与预算科目映射"},
    ],
}


def build_plan(manifest):
    module = manifest["module"]
    revision, standards = _matrix(module)
    groups, unmapped = {}, []
    for row in manifest["records"]:
        key = str(row["check_order"]), str(row["research_id"])
        if key not in standards or not row.get("mapped", False):
            unmapped.append(row)
        else:
            groups.setdefault(key, []).append(row)
    duplicates = sorted(key for key, values in groups.items() if len(values) != 1)
    return {"module": module, "matrix_revision": revision, "standards": standards,
            "sources": {key: values[0] for key, values in groups.items() if len(values) == 1},
            "duplicates": duplicates, "unmapped": unmapped,
            "missing": sorted(set(standards) - {key for key, values in groups.items() if len(values) == 1})}


def temporal_counts(rows, fields, as_of):
    start, end, flag = fields
    counts = Counter()
    for row in rows:
        if row.get(end) and row[end] < as_of:
            status = "expired"
        elif row.get(start) and row[start] > as_of:
            status = "future"
        else:
            status = "within_date_range"
        counts[status] += 1
        if flag is not None and status == "within_date_range" and row.get(flag):
            counts["enabled_within_date_range"] += 1
    return dict(counts)


def policy_line_effective_count(rows, as_of):
    return sum((not row.get("effective_date") or row["effective_date"] <= as_of)
               and (not row.get("expiry_date") or row["expiry_date"] >= as_of) for row in rows)


def permission_validity(rows, as_of):
    # is_active 是按服务器今天计算的状态，不是独立的启用开关。
    return {"validity": temporal_counts(rows, ("effective_date", "expiration_date", None), as_of),
            "current_date_state": {"is_active_true": sum(bool(row["is_active"]) for row in rows),
                                   "is_active_false": sum(not row["is_active"] for row in rows)},
            "note": "validity 按目标业务日期及起止日推算；current_date_state 是服务器今天的 is_active，不能用于历史或未来有效性判断"}


def validate_manifest(manifest):
    coverage = manifest.get("coverage", {})
    records = manifest.get("records", [])
    assert coverage.get("source_subitems") == len(records)
    mapped = sum(bool(row.get("mapped")) for row in records)
    assert coverage.get("mapped_subitems") == mapped
    assert coverage.get("unmapped_subitems") == len(records) - mapped
    assert re.fullmatch(r"[a-f0-9]{64}", manifest.get("source_sha256", ""))
    assert records and all(row.get("source", {}).get("file") and row["source"].get("sheet")
                           and row["source"].get("cell") for row in records)
    assert all(row["source"].get("sha256") == manifest["source_sha256"] for row in records)
    assert manifest.get("matrix_revision") == _matrix(manifest["module"])[0], "矩阵已变化，应重新核对来源映射"
    return coverage


def source_company_scope(manifest):
    """仅接受明确的正整数公司 ID；布尔值和空列表不构成已确认范围。"""
    ids = manifest.get("company_ids")
    return ids if isinstance(ids, list) and ids and all(type(item) is int and item > 0 for item in ids) else []


def structure_ids_for_companies(structure_lines):
    return sorted({row["head_id"][0] for row in structure_lines if row.get("head_id")})


def policy_name_from_source(matched):
    names = {match for row in matched for match in re.findall(r"[“\"]([^”\"]+)[”\"]政策头", row["conclusion"])}
    if len(names) != 1:
        raise ValueError("来源未唯一指定政策头，不能把全部政策当作同一政策比较")
    return next(iter(names))


SHARED_CONFIG_MODELS = {"dy.expense.city.season"}
OWNER_COMPANY_MODELS = {"dy.fin.account.analytic.plan", "dy.fin.cash.flow.mapping", "account.mapping"}


def scoped_domain(model, metadata, company_ids, structure_ids):
    if model == "res.company":
        return [["id", "in", company_ids]]
    if model == "dy.expense.policy.line":
        # 调用方必须按先前查得的政策头追加 expense_policy 范围。
        return []
    if model == "dy.fin.account.cash.flow.statement.entry":
        if "account_structure_id" not in metadata:
            raise ValueError("现金流量表项缺少已知范围字段 account_structure_id")
        return ["|", ["account_structure_id", "=", False],
                ["account_structure_id", "in", structure_ids]]
    if model == "account.journal":
        if not {"account_structure_id", "journal_company_ids"} <= metadata.keys():
            raise ValueError("分类账缺少已知范围字段，不能用 company_id 替代适用范围")
        return [["account_structure_id", "in", structure_ids],
                "|", ["journal_company_ids", "=", False], ["journal_company_ids", "in", company_ids]]
    if model == "dy.expense.document.type" and "company_ids" in metadata:
        return ["|", ["company_ids", "=", False], ["company_ids", "in", company_ids]]
    if model in OWNER_COMPANY_MODELS:
        if "company_id" not in metadata:
            raise ValueError("模型缺少已知范围字段 company_id，不能使用候选公司列表替代")
        return [["company_id", "in", company_ids]]
    if "company_ids" in metadata:
        return [["company_ids", "in", company_ids]]
    if "company_id" in metadata:
        return [["company_id", "in", company_ids]]
    if "company_distribution_ids" in metadata:
        return [["company_distribution_ids", "in", company_ids]]
    if "account_structure_id" in metadata:
        return [["account_structure_id", "in", structure_ids]]
    if model in SHARED_CONFIG_MODELS:
        return []
    raise ValueError("模型缺少已知范围字段，不能执行无界限记录查询")


async def source_workflow(workspace, manifest, connection, as_of, *, diagnostic_visible_scope=False):
    plan = build_plan(manifest)
    validate_manifest(manifest)
    module = plan["module"]
    scope_company_ids = source_company_scope(manifest)
    trace_id = f"source-e2e-{module}"
    report = _report(module, "SOURCE-E2E-READONLY", result="待确认",
                     reason="来源未提供可追溯业务签认，候选要求与配置观察不能代替最终验收")
    for item in report["records"]:
            source = plan["sources"].get((item["check_order"], item["research_id"]))
            conflict = next((entry for entry in manifest.get("acceptance_conflicts", [])
                             if str(entry["check_order"]) == item["check_order"]
                             and str(entry["research_id"]) == item["research_id"]), None)
            if source:
                ref = source["source"]
                item["reason"] = f"来源：{ref['file']}#{ref.get('sheet', '')}!{ref.get('cell', '')}；业务签认、范围及完整样例仍待核实"
            if conflict:
                item["reason"] += f"；外部验收标准与当前矩阵不一致（来源 {conflict['source']['sheet']}!{conflict['source']['cell']}），按当前矩阵标准仍阻塞复核"
            if any(key == (item["check_order"], item["research_id"]) for key in plan["duplicates"]):
                item["reason"] += "；同一来源键有重复或冲突记录，阻塞自动映射"
            if any(row.get("check_order") == item["check_order"] and row.get("research_id") == item["research_id"]
                   for row in plan["unmapped"]):
                item["reason"] += "；来源子项未映射到当前矩阵，阻塞该子项"
    summary = {"module": module, "as_of": as_of, "source_coverage": manifest["coverage"],
               "source_sha256": manifest["source_sha256"], "checks": [], "gaps": [],
               "source_scope_confirmed": manifest.get("company_scope_confirmed") is True,
               "source_environment_confirmed": manifest.get("environment_confirmed") is True,
               "source_acceptance_conflicts": manifest.get("coverage", {}).get("acceptance_conflicts", 0),
               "duplicates": [list(key) for key in plan["duplicates"]],
               "missing_source_keys": [list(key) for key in plan["missing"]], "unmapped": [
                   {"key": f"{row['source_check_order']}:{row['research_id']}", "source": row["source"]}
                   for row in plan["unmapped"]], "confirmed_research_written": 0,
               "limitations": "数量相符仅是配置快照对照，待确认/未签认来源不产生最终通过结论"}
    async with stdio_client(_parameters(workspace, connection)) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()

            async def call(tool, **arguments):
                response = await session.call_tool(tool, {"trace_id": trace_id, **arguments})
                assert not response.model_dump(by_alias=True).get("isError", False)
                text = response.content[0].text
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text

            await call("dy_session_initialize", source="startup")
            if scope_company_ids:
                company_ids = scope_company_ids
            elif diagnostic_visible_scope:
                companies = await call("dy_odoo_record_search", model="res.company", args=[[]],
                                       kwargs={"fields": ["id"], "limit": 100})
                assert len(companies) < 100, "公司范围列表满页，需显式提供范围后重试"
                company_ids = [row["id"] for row in companies]
            else:
                company_ids = []
            summary["diagnostic_company_count"] = len(company_ids)
            summary["diagnostic_visible_scope"] = diagnostic_visible_scope
            structure_ids = []
            if company_ids:
                structures = await call("dy_odoo_record_search", model="dy.fin.account.structure.line",
                                        args=[[["company_id", "in", company_ids]]],
                                        kwargs={"fields": ["head_id"], "limit": 100})
                assert len(structures) < 100, "结构行列表满页，范围不完整"
                structure_ids = structure_ids_for_companies(structures)
            policy_ids = None
            for recipe in RECIPES[module]:
                matched = [row for key, row in plan["sources"].items() if key[0] == recipe["order"]]
                if not matched:
                    continue
                source = matched[0]
                entry = {"order": recipe["order"], "model": recipe["model"],
                         "sources": [row["source"] for row in matched], "status": "待确认"}
                try:
                    metadata = await call("dy_odoo_model_fields", model=recipe["model"])
                    required_fields = set(recipe["fields"]) | {term[0] for term in recipe["domain"]}
                    if recipe.get("policy_head"):
                        required_fields.add("name")
                    missing = required_fields - metadata.keys()
                    if missing:
                        entry.update(status="配方字段不匹配", missing_fields=sorted(missing))
                        summary["checks"].append(entry)
                        continue
                    if not company_ids:
                        entry["status"] = "仅核对字段：未指定公司范围，记录查询阻塞"
                        summary["checks"].append(entry)
                        continue
                    domain = [[field, operation, as_of if value == "{as_of}" else value]
                              for field, operation, value in recipe["domain"]]
                    domain.extend(scoped_domain(recipe["model"], metadata, company_ids, structure_ids))
                    if recipe.get("policy_head"):
                        domain.append(["name", "=", policy_name_from_source(matched)])
                    if recipe.get("policy_scope"):
                        # 950 条是指定政策头下的明细；先查头，再按真实 Many2one expense_policy 过滤。
                        if policy_ids is None:
                            raise AssertionError("政策明细查询必须先完成政策头查询")
                        domain.append(["expense_policy", "in", policy_ids])
                    context = {"skill": f"{module}-inspection", "module": module,
                               "check_order": source["check_order"], "research_id": source["research_id"],
                               "environment": "SOURCE-E2E-READONLY"}
                    # 来源未确认，不编造调研 revision 或 scope_id 放进 trace_context。
                    rows, offsets, identifiers = [], [], set()
                    for offset in range(0, 10000, 100):
                        page = await call("dy_odoo_record_search", model=recipe["model"], args=[domain],
                                          kwargs={"fields": recipe["fields"], "limit": 100, "offset": offset},
                                          trace_context=context)
                        assert isinstance(page, list) and len(page) <= 100
                        page_ids = {item["id"] for item in page}
                        assert not identifiers & page_ids, "分页中出现重复 ID；不能声称取全量"
                        identifiers.update(page_ids)
                        rows.extend(page)
                        offsets.append(offset)
                        if len(page) < 100:
                            break
                    else:
                        raise AssertionError("超过查询上限，不能声称取全量")
                    entry.update(actual_count=len(rows), offsets=offsets, fields=recipe["fields"],
                                 status="已读取配置快照，业务验收待确认",
                                 comparison_scope="仅当前账号可见范围；来源公司/环境未确认，数量对照仅供诊断")
                    if recipe.get("policy_head"):
                        policy_ids = [row["id"] for row in rows]
                        entry["selected_policy_name"] = policy_name_from_source(matched)
                    pattern = recipe.get("expected")
                    numbers = {int(number) for row in matched for number in re.findall(pattern, row["conclusion"])} if pattern else set()
                    if len(numbers) == 1:
                        expected = next(iter(numbers))
                        comparable = (bool(scope_company_ids) and manifest.get("company_scope_confirmed") is True
                                      and manifest.get("environment_confirmed") is True)
                        entry.update(source_count=expected, count_comparison=("相符" if expected == len(rows) else "差异")
                                     if comparable else "不可直接比较：来源环境及范围未确认")
                    elif len(numbers) > 1:
                        entry.update(count_comparison="来源数量冲突", source_counts=sorted(numbers))
                    if recipe["model"] == "dy.expense.city.season":
                        entry["relation_counts"] = {"low_season_links": sum(len(row["low_season_ids"]) for row in rows),
                                                    "peak_season_links": sum(len(row["peak_season_ids"]) for row in rows)}
                    if recipe["model"] == "dy.budget.control.execution.rule":
                        entry["validity"] = temporal_counts(rows, ("date_from", "date_to", "is_active_flag"), as_of)
                    elif recipe["model"] == "dy.hrfm.expense.execute.rule":
                        entry["validity"] = temporal_counts(rows, ("date_from", "date_to", "active_flag"), as_of)
                        entry["by_document"] = {name: temporal_counts([row for row in rows if row["source_doc"] == name],
                                                                     ("date_from", "date_to", "active_flag"), as_of)
                                                 for name in sorted({row["source_doc"] for row in rows if isinstance(row["source_doc"], str)})}
                    elif recipe["model"] == "dy.expense.permission.configuration":
                        entry.update(permission_validity(rows, as_of))
                    elif recipe["model"] == "dy.fin.account.period":
                        entry["states"] = dict(Counter(row["state"] for row in rows))
                        if module == "expense":
                            entry["expense_states"] = dict(Counter(row["expense_period_status"] for row in rows))
                            entry["note"] = "as_of 仅筛选当前库中该业务日期的期间及规则；并非恢复来源核查日的历史数据库快照"
                    elif recipe["model"] == "dy.expense.policy.line":
                        entry["control_levels"] = dict(Counter(str(row["control_level"]) for row in rows))
                        entry["effective_count"] = policy_line_effective_count(rows, as_of)
                    for item in report["records"]:
                        if item["check_order"] == recipe["order"]:
                            item["reason"] += f"；{recipe['model']} 已分页读取 {len(rows)} 条，尚未替代公司/制度/流程验收"
                            if entry.get("count_comparison") == "差异":
                                item["reason"] += f"；来源记载 {entry['source_count']} 条，当前可见范围 {len(rows)} 条，需核实环境/日期/权限范围"
                    if entry.get("count_comparison") == "差异":
                        source_count, actual_count = entry["source_count"], len(rows)
                        summary["gaps"].append({"code": "count_mismatch", "model": recipe["model"],
                                                "source_count": source_count, "readonly_count": actual_count,
                                                "direction": "fewer" if actual_count < source_count else "more",
                                                "resolution": "未判断为缺陷；核对来源时点、公司范围、字段筛选及账号权限"})
                except MCPError as exc:
                    entry.update(status="未知：模型读取或权限失败", error_code=exc.code)
                summary["checks"].append(entry)
            report["project_context"] = {"company_scope_confirmed": False,
                                         "environment": "SOURCE-E2E-READONLY"}
            report["evidence_summary"] = {"summary": (
                f"已解析 {manifest['coverage']['source_subitems']} 个来源子项；"
                f"映射 {len(plan['sources'])} 个，未映射 {len(plan['unmapped'])} 个；"
                f"执行 {len(summary['checks'])} 个只读配置配方；正式可比的数量差异 {len(summary['gaps'])} 个。"
                f"外部验收标准冲突 {summary['source_acceptance_conflicts']} 个，重复键 {len(plan['duplicates'])} 个。"
                "公司/环境/来源时点未签认，当前可见数量不可直接与来源汇总比较，不据此判配置缺陷。")}
            report["source_blockers"] = {"unmapped": summary["unmapped"],
                                         "acceptance_conflicts": manifest.get("acceptance_conflicts", []),
                                         "duplicates": summary["duplicates"],
                                         "missing_source_keys": summary["missing_source_keys"]}
            report["diagnostic_checks"] = summary["checks"]
            report["next_steps"] = ["核对来源文件对应数据库、版本、公司范围及查询账号权限",
                                    "先确认业务要求和例外，再对配置差异逐项复查",
                                    "页面、流程、权限反例、政策算例继续待验证；不自动补配或续期历史规则"]
            validation = await call("dy_inspection_validate", report=report)
            assert validation["valid"] and validation["gate"] == "待确认"
            saved = await call("dy_inspection_report_save", report=report, confirmed=True)
            text = await call("dy_inspection_report_read", module=module, report_id=saved["report_id"])
            assert text == (workspace / saved["path"]).read_text(encoding="utf-8")
            assert not (workspace / ".dy_superpowers/modules").exists()
            trace = await call("dy_trace_read")
            secret = connection["APP_PASSWORD"]
            assert not secret or secret not in json.dumps(trace, ensure_ascii=False)
            summary.update(gate=validation["gate"], report_path=saved["path"],
                           report_records=len(report["records"]), remote_writes=False,
                           limitations=validation["limitations"] + "；来源未签认，配置相符也不等于验收通过")
    (workspace / "source-e2e-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def test_manifest_integrity_requires_source_locations_and_matrix_revision():
    revision = _matrix("expense")[0]
    manifest = {"module": "expense", "source_sha256": "a" * 64,
                "matrix_revision": revision,
                "coverage": {"source_subitems": 1, "mapped_subitems": 1, "unmapped_subitems": 0},
                "records": [{"mapped": True, "source": {"file": "source.xlsx", "sheet": "Sheet1", "cell": "G4", "sha256": "a" * 64}}]}
    assert validate_manifest(manifest)["source_subitems"] == 1
    broken = {**manifest, "coverage": {**manifest["coverage"], "mapped_subitems": 0}}
    with pytest.raises(AssertionError):
        validate_manifest(broken)


def test_source_company_scope_requires_explicit_positive_ids():
    assert source_company_scope({"company_ids": [1, 2]}) == [1, 2]
    assert source_company_scope({"company_ids": [True]}) == []
    assert source_company_scope({"company_ids": ["1"]}) == []
    assert source_company_scope({"company_ids": []}) == []


def test_scoped_domain_never_uses_unknown_model_as_unbounded_query():
    assert scoped_domain("res.company", {"id": {}}, [7], []) == [["id", "in", [7]]]
    assert scoped_domain("dy.expense.document.type", {"company_ids": {}}, [7], []) == [
        "|", ["company_ids", "=", False], ["company_ids", "in", [7]]]
    assert scoped_domain("account.mapping", {"company_id": {}}, [7], []) == [["company_id", "in", [7]]]
    assert scoped_domain("dy.expense.city.season", {}, [7], []) == []
    with pytest.raises(ValueError, match="范围字段"):
        scoped_domain("unknown.model", {}, [7], [])


def test_plan_preserves_extra_source_item_and_does_not_confirm():
    manifest = {"module": "expense", "records": [
        {"check_order": "1", "research_id": "01", "mapped": True, "conclusion": "需要安装，不代表已安装"},
        {"check_order": "28", "research_id": "99", "source_check_order": "DY-28", "mapped": False},
    ]}
    plan = build_plan(manifest)
    assert len(plan["unmapped"]) == 1
    assert ("1", "01") in plan["sources"]
    assert len(plan["standards"]) == 92
    assert "confirmed" not in manifest["records"][0]


def test_plan_does_not_pick_one_of_conflicting_source_rows():
    row = {"check_order": "1", "research_id": "01", "mapped": True}
    plan = build_plan({"module": "expense", "records": [dict(row, conclusion="需要"), dict(row, conclusion="不需要")]})
    assert plan["duplicates"] == [("1", "01")]
    assert ("1", "01") not in plan["sources"]
    assert ("1", "01") in plan["missing"]


def test_policy_name_is_derived_from_source_not_all_policies():
    assert policy_name_from_source([{"conclusion": "需存在1个“住宿费”政策头及950条有效明细"}]) == "住宿费"
    assert policy_name_from_source([{"conclusion": '需存在1个"交通费"政策头'}]) == "交通费"
    with pytest.raises(ValueError, match="未唯一指定"):
        policy_name_from_source([{"conclusion": "本期按制度核验政策"}])
    with pytest.raises(ValueError, match="未唯一指定"):
        policy_name_from_source([{"conclusion": "1个“住宿费”政策头，1个“交通费”政策头"}])


def test_policy_detail_recipe_uses_real_parent_field():
    recipe = next(row for row in RECIPES["expense"] if row.get("policy_scope"))
    assert "expense_policy" in recipe["fields"]
    assert "expense_item_id" not in recipe["fields"]


def test_policy_detail_count_uses_business_date():
    rows = [
        {"effective_date": "2025-01-01", "expiry_date": "2026-10-22"},
        {"effective_date": "2026-10-23", "expiry_date": False},
        {"effective_date": False, "expiry_date": "2026-10-21"},
    ]
    assert policy_line_effective_count(rows, "2026-10-22") == 1


def test_source_queries_use_explicit_record_relations():
    structure_recipe = next(row for row in RECIPES["general-ledger"]
                            if row["model"] == "dy.fin.account.structure")
    assert "company_distribution_ids" not in structure_recipe["fields"]
    assert structure_ids_for_companies([{"head_id": [12, "财务账套"]}, {"head_id": [15, "预算账套"]},
                                        {"head_id": False}]) == [12, 15]
    assert scoped_domain("dy.fin.account.cash.flow.statement.entry",
                         {"account_structure_id": {}}, [7], [12]) == [
        "|", ["account_structure_id", "=", False], ["account_structure_id", "in", [12]]]
    expense_rules = next(row for row in RECIPES["expense"]
                         if row["model"] == "dy.hrfm.expense.execute.rule")
    assert expense_rules["domain"] == [["source_doc", "in", [
        "expense_report", "expense_payment", "expense_requisition"]]]


def test_general_ledger_reference_distinguishes_finance_and_budget_carry_forward():
    reference = Path(__file__).parents[2] / "skills/general-ledger-inspection/references/source-queries.md"
    content = reference.read_text(encoding="utf-8")
    assert "carry_forward_account_move_id" in content and "account.move" in content
    assert "carry_forward_budget_move_id" in content and "budget.account.move" in content
    assert "财务结转通过 `carry_forward_account_move_id` 回链生成的 `account.move`" in content
    assert "预算结转通过 `carry_forward_budget_move_id` 回链生成的 `budget.account.move`" in content
    assert "周期模板结转" not in content


def test_temporal_rules_do_not_treat_enabled_expired_as_effective():
    rows = [dict(start="2023-01-01", end="2024-12-31", enabled=True),
            dict(start="2025-11-01", end="2028-12-31", enabled=True),
            dict(start="2027-01-01", end=False, enabled=True)]
    assert temporal_counts(rows, ("start", "end", "enabled"), "2026-10-22") == {
        "expired": 1, "within_date_range": 1, "enabled_within_date_range": 1, "future": 1}


@pytest.mark.skipif(not (os.environ.get("DY_E2E_RESEARCH_SOURCES") and os.environ.get("DY_E2E_ODOO_ENV_FILE")),
                    reason="真实调研及 Odoo 端测需显式提供本地来源目录和环境配置")
@pytest.mark.parametrize("module", RECIPES)
def test_live_inspection_from_supplied_research(tmp_path, module):
    from dotenv import dotenv_values

    source_dir = Path(os.environ["DY_E2E_RESEARCH_SOURCES"]).resolve()
    manifest = json.loads((source_dir / f"{module}-source.json").read_text(encoding="utf-8"))
    assert manifest["module"] == module
    source_path = source_dir / manifest["source_file"]
    supplied_path = os.environ.get("DY_E2E_SOURCE_FILE_EXPENSE" if module == "expense" else "DY_E2E_SOURCE_FILE_LEDGER")
    if supplied_path:
        source_path = Path(supplied_path).resolve()
    if not source_path.is_file():
        pytest.fail("需通过 DY_E2E_SOURCE_FILE_EXPENSE/LEDGER 显式提供原始调研文件，以核验来源哈希")
    assert hashlib.sha256(source_path.read_bytes()).hexdigest() == manifest["source_sha256"], "原始来源已变化，应重新解析"
    validate_manifest(manifest)
    values = dotenv_values(Path(os.environ["DY_E2E_ODOO_ENV_FILE"]).resolve())
    keys = ("APP_URL", "APP_URL", "DB_NAME", "APP_USER", "APP_PASSWORD")
    if any(not isinstance(values.get(key), str) or not values[key] for key in keys):
        pytest.fail("缺少显式连接配置，不输出配置值")
    as_of = os.environ.get("DY_E2E_AS_OF", datetime.now(timezone.utc).date().isoformat())
    datetime.strptime(as_of, "%Y-%m-%d")
    workspace = tmp_path / module
    workspace.mkdir()
    summary = asyncio.run(asyncio.wait_for(source_workflow(
        workspace, manifest, {key: values[key] for key in keys}, as_of,
        diagnostic_visible_scope=os.environ.get("DY_E2E_VISIBLE_SCOPE_DIAGNOSTIC") == "1",
    ), timeout=600))
    assert summary["gate"] == "待确认"
    assert summary["confirmed_research_written"] == 0
    assert summary["remote_writes"] is False
    assert summary["report_records"] == len(_matrix(module)[1])
    assert summary["checks"], "来源没有匹配任何配方，不能称为端到端诊断"
    if source_company_scope(manifest) or os.environ.get("DY_E2E_VISIBLE_SCOPE_DIAGNOSTIC") == "1":
        assert any("actual_count" in item for item in summary["checks"]), "没有成功记录查询，不能称为配置端测通过"
    technical_failures = [item for item in summary["checks"]
                          if item.get("missing_fields") or item.get("error_code")]
    assert not technical_failures, "技术配方失败，详见本次 source-e2e-summary.json"
