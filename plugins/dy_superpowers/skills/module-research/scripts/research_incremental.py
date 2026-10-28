"""调研来源增量导入：差异分类 → 只写增量 → 回读校验并登记导入台账。

与 research_sources.py 配合：把解析出的候选结论与当前工作区调研记忆按
检查键 + 适用范围比对，输出 new / changed / unchanged / removed_in_source /
blocked 五类。写入仍由 dy_research_record_write 经用户确认执行；本脚本不写
调研记录，--record 仅在回读校验通过后登记导入台账（imports.json）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parents[2] / "mcp-server" / "src"))

from research_sources import parse_source  # noqa: E402
from dy_superpowers_mcp.memory import validate_module  # noqa: E402
from dy_superpowers_mcp.research import _scope_text, read_module_records  # noqa: E402
from dy_superpowers_mcp.storage import atomic_write, record_lock  # noqa: E402

# 只剥离行首“01｜”子项编号；SUBITEM 正则匹配整行，不能直接用于去前缀。
SUBITEM_PREFIX = re.compile(r"^\s*\d{1,3}\s*[｜|．.、]\s*")


def _strip_subitem(conclusion: str) -> str:
    return SUBITEM_PREFIX.sub("", conclusion or "", count=1).strip()


def _evidence_ref(row: dict) -> str:
    """保留附件文件名及解析器提供的页/表/单元格定位，避免只回链整份附件。"""
    base = (row.get("suggested_evidence") or "").strip()
    locations = []
    for ref in row.get("detail_refs") or []:
        for locator in ref.get("locations") or []:
            if not isinstance(locator, dict):
                continue
            parts = []
            for key in ("sheet", "page", "line", "table_row", "cell"):
                if locator.get(key) not in (None, ""):
                    parts.append(f"{key}={locator[key]}")
            if parts:
                locations.append("/".join(parts))
    if locations:
        suffix = "；定位：" + ",".join(dict.fromkeys(locations))
        truncated = sum(max(0, (ref.get("location_count") or len(ref.get("locations") or []))
                            - len(ref.get("locations") or []))
                        for ref in row.get("detail_refs") or [] if ref.get("locations_truncated"))
        if truncated:
            suffix += f"；另有{truncated}条附件定位未展开"
        return base + suffix if base else suffix.removeprefix("；")
    return base

MATRIX_MODULES = {"expense", "general-ledger"}
SOURCE_HINTS = {
    "multiple_conclusion_tables": "来源包含多个结论表，已按解析器规则择优解析；请核对覆盖范围",
    "hidden_sheet": "来源包含隐藏工作表，解析器仍扫描其内容；请核对可见性与适用范围",
}


def _load_records(workspace: Path, module: str) -> list[dict]:
    records, offset = [], 0
    while True:
        batch, more = read_module_records(workspace, module, offset=offset, limit=100)
        records.extend(batch)
        if not more:
            return records
        offset += 100


def _by_key(records: list[dict]) -> dict[tuple[str, str], list[dict]]:
    grouped: dict[tuple[str, str], list[dict]] = {}
    for record in records:
        grouped.setdefault((record["check_order"], record["research_id"]), []).append(record)
    return grouped


def ledger_path(workspace: Path, module: str) -> Path:
    return Path(workspace) / ".dy_superpowers" / "modules" / module / "imports.json"


def load_ledger(workspace: Path, module: str) -> list[dict]:
    path = ledger_path(workspace, module)
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
        raise ValueError("导入台账损坏：必须是对象列表；请人工核对后修复，不得猜测")
    return data


def _latest_entry(ledger: list[dict], source_name: str) -> dict | None:
    for entry in reversed(ledger):
        if entry.get("source_name") == source_name:
            return entry
    return None


def _matrix_keys(module: str) -> set | None:
    if module not in MATRIX_MODULES:
        return None
    from dy_superpowers_mcp.inspection import _matrix
    _, keys = _matrix(module)
    return keys


def _resolve_scope(row: dict, current: list[dict], default_scope: str) -> tuple[str, str | None]:
    """返回（范围文本，阻塞原因）；规则适用条件不是项目实际范围。"""
    scope = (row.get("scope") or "").strip() or (default_scope or "").strip()
    if scope:
        return scope, None
    if len(current) == 1:
        return current[0]["scope"], None
    if len(current) > 1:
        return "", "该检查键有多个适用范围记录，来源未区分适用范围，不能猜测"
    return "", "缺少适用范围：来源无适用条件，也未提供默认范围（--scope）"


def apply_mapping(candidates: list[dict], mapping: list) -> tuple[list[dict], list[dict]]:
    """按问题文本把 QA 候选映射到矩阵检查键；映射由助手生成、用户确认，映射外问题不猜。"""
    by_question = {}
    for item in mapping:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question", "")).strip()
        if question in by_question:
            raise ValueError("映射含重复问题，无法消除适用范围歧义；请区分来源问题后重新映射")
        by_question[question] = item
    seen = set()
    for row in candidates:
        locator = row.get("locator") if isinstance(row.get("locator"), dict) else {}
        question = str(locator.get("question", "")).strip()
        if question and question in by_question and question in seen:
            raise ValueError("来源含重复问题，仅按问题文本映射存在歧义；请区分来源问题后重新映射")
        seen.add(question)
    applied, unmatched = [], []
    for row in candidates:
        locator = row.get("locator") if isinstance(row.get("locator"), dict) else {}
        matched = by_question.get(str(locator.get("question", "")).strip())
        if matched is None:
            unmatched.append(row)
            continue
        row = dict(row)
        row["check_order"] = str(matched.get("check_order", "")).strip()
        row["research_id"] = str(matched.get("research_id", "")).strip()
        if matched.get("scope") and not (row.get("scope") or "").strip():
            row["scope"] = str(matched["scope"]).strip()
        applied.append(row)
    return applied, unmatched


def _matches_source(current: dict, row: dict) -> bool:
    """分类与回读登记使用同一组结论、状态、依据和配置项比较。"""
    return (current["conclusion"] == _strip_subitem(row.get("conclusion") or "")
            and current["status"] == "confirmed"
            and current.get("evidence_ref", "") == _evidence_ref(row)
            and current["config_item"] == (row.get("config_item") or ""))


def classify(workspace: Path, module: str, candidates: list[dict], *, default_scope: str = "") -> dict:
    matrix_keys = _matrix_keys(module)
    grouped = _by_key(_load_records(workspace, module))
    result = {"new": [], "changed": [], "unchanged": [], "blocked": []}
    for row in candidates:
        order = (row.get("check_order") or "").strip()
        research_id = (row.get("research_id") or "").strip()
        is_qa = isinstance(row.get("locator"), dict) and row["locator"].get("kind") == "qa"
        reasons = []
        if row.get("contains_pending_questions"):
            reasons.append("来源仍含待确认内容，不能分类为可导入结论")
        if not order:
            reasons.append("QA 未映射矩阵检查键，须生成映射并经用户确认" if is_qa else "缺少检查顺序")
        if not research_id:
            reasons.append("缺少调研子项编号（QA 须在映射中给出 research_id）" if is_qa
                           else "缺少调研子项编号（结论未按“01｜”样式编号）")
        if matrix_keys is not None and (order, research_id) not in matrix_keys:
            reasons.append("检查键不在当前模块矩阵")
        current = grouped.get((order, research_id), []) if order and research_id else []
        scope, scope_reason = _resolve_scope(row, current, default_scope)
        if scope_reason:
            reasons.append(scope_reason)
        if reasons:
            result["blocked"].append({"check_order": order, "research_id": research_id,
                                      "config_item": row.get("config_item") or "",
                                      "locator": row.get("locator"),
                                      "conclusion": row.get("conclusion") or "", "reasons": reasons})
            continue
        # 结论去掉行首“01｜”子项编号后再比对；编号本身已进入 research_id。
        conclusion = _strip_subitem(row.get("conclusion") or "")
        entry = {"check_order": order, "research_id": research_id,
                 "config_item": row.get("config_item") or "", "scope": scope,
                 "conclusion": conclusion, "evidence_ref": _evidence_ref(row),
                 "locator": row.get("locator")}
        matched = next((r for r in current if _scope_text(r["scope"]) == _scope_text(scope)), None)
        if matched is None:
            result["new"].append(entry)
            continue
        entry["scope_id"] = matched["scope_id"]
        if _matches_source(matched, row):
            result["unchanged"].append(entry)
        else:
            entry["expected_revision"] = matched["revision"]
            entry["current"] = {"conclusion": matched["conclusion"], "status": matched["status"],
                                "evidence_ref": matched.get("evidence_ref", ""),
                                "config_item": matched["config_item"]}
            result["changed"].append(entry)
    return result


def removed_in_source(workspace: Path, module: str, source_name: str, candidates: list[dict],
                      *, default_scope: str = "") -> list[dict]:
    previous = _latest_entry(load_ledger(workspace, module), source_name)
    if not previous:
        return []
    grouped = _by_key(_load_records(workspace, module))
    present, unresolved = set(), set()
    for row in candidates:
        key = ((row.get("check_order") or "").strip(), (row.get("research_id") or "").strip())
        scope, reason = _resolve_scope(row, grouped.get(key, []), default_scope)
        if reason:
            unresolved.add(key)  # 范围不明确时由 blocked 提示，不能猜测已移除哪些范围。
        else:
            present.add((*key, _scope_text(scope)))
    removed = []
    for item in previous.get("keys", []):
        key = (item.get("check_order"), item.get("research_id"))
        if key not in unresolved and (*key, _scope_text(item.get("scope") or "")) not in present:
            removed.append({"check_order": item.get("check_order"),
                            "research_id": item.get("research_id"),
                            "scope": item.get("scope"), "scope_id": item.get("scope_id"),
                            "note": "新版本来源已移除该子项；是否停用须业务确认，不自动停用"})
    return removed


def record_import(workspace: Path, module: str, source_name: str, source_sha256: str,
                  candidates: list[dict], *, default_scope: str = "") -> dict:
    """按范围回读，结论、状态、依据和配置项全部匹配后才登记台账。"""
    grouped = _by_key(_load_records(workspace, module))
    problems, keys = [], []
    for row in candidates:
        order = (row.get("check_order") or "").strip()
        research_id = (row.get("research_id") or "").strip()
        current = grouped.get((order, research_id), [])
        scope, reason = _resolve_scope(row, current, default_scope)
        matched = (next((r for r in current if _scope_text(r["scope"]) == _scope_text(scope)), None)
                   if scope else None)
        if matched is None:
            problems.append({"check_order": order, "research_id": research_id,
                             "reason": reason or "未找到按检查键与适用范围匹配的当前记录"})
            continue
        if not _matches_source(matched, row):
            problems.append({"check_order": order, "research_id": research_id,
                             "reason": "回读结论、依据或配置项与来源不一致，或状态不是 confirmed，导入未完成"})
            continue
        keys.append({"check_order": order, "research_id": research_id,
                     "scope": matched["scope"], "scope_id": matched["scope_id"]})
    for removed in removed_in_source(workspace, module, source_name, candidates, default_scope=default_scope):
        current = grouped.get((removed["check_order"], removed["research_id"]), [])
        matched = next((row for row in current if
                        (row["scope_id"] == removed["scope_id"] if removed.get("scope_id")
                         else _scope_text(row["scope"]) == _scope_text(removed["scope"]))), None)
        if matched is None or matched["status"] != "retired":
            problems.append({**removed, "reason": "来源移除项尚未由业务确认停用为 retired，不能推进台账；不自动停用"})
    if problems:
        return {"recorded": False, "problems": problems}
    path = ledger_path(workspace, module)
    path.parent.mkdir(parents=True, exist_ok=True)
    with record_lock(path):
        ledger = load_ledger(workspace, module)
        ledger.append({"source_name": source_name, "source_sha256": source_sha256,
                       "imported_at": datetime.now(timezone.utc).isoformat(), "keys": keys})
        atomic_write(path, json.dumps(ledger, ensure_ascii=False, indent=2) + "\n")
    return {"recorded": True, "keys": len(keys)}


def _source_problems(data: dict) -> list[dict]:
    """仅白名单中的结构提示不阻塞；公式缺缓存、丢失回答等解析问题必须处置。"""
    issues, coverage = data.get("issues"), data.get("coverage")
    if (not isinstance(issues, list) or any(not isinstance(item, dict) for item in issues)
            or not isinstance(coverage, dict)
            or type(coverage.get("source_subitems")) is not int
            or coverage["source_subitems"] < 0):
        raise ValueError("候选缺少有效 issues/coverage 完整性元数据，请重新解析来源")
    problems = [{"issue": item, "reason": f"来源解析存在未解决问题：{item.get('code', 'unknown')}，请核实并重新解析"}
                for item in issues if item.get("code") not in SOURCE_HINTS]
    if coverage["source_subitems"] != len(data["records"]):
        problems.append({"reason": "来源覆盖数与候选记录数不一致，请核实并重新解析"})
    return problems


def _candidates_from(source: Path | None, candidates_file: Path | None, module: str,
                     *, metadata: dict | None = None) -> tuple[list[dict], str, str]:
    if candidates_file is not None:
        data = json.loads(candidates_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("records"), list):
            raise ValueError("候选 JSON 必须是 research_sources.py 的解析产物")
        # 旧解析产物可能把已拒绝附件复制到依据和详情中；不能绕过新解析器继续导入。
        for row in data["records"]:
            values = [row.get("suggested_evidence") or ""]
            values.extend(ref.get("attachment") or "" for ref in row.get("detail_refs", []))
            if any(re.search(r"//[^/?#]*@", value.translate(str.maketrans("", "", "\r\n\t")))
                   for value in values):
                raise ValueError("候选包含凭据链接，请移除凭据并重新解析来源；不得复用旧候选")
        data.setdefault("source_file", candidates_file.name)
    else:
        data = parse_source(source, module)
    problems = _source_problems(data)
    if metadata is not None:
        metadata.update({"issues": data["issues"], "coverage": data["coverage"], "problems": problems})
    elif problems:
        raise ValueError("来源解析存在完整性缺口，请核实并重新解析；不得丢弃问题继续导入")
    return data["records"], data.get("source_file") or candidates_file.name, data.get("source_sha256", "")


def _candidate_integrity(source: Path | None, candidates_file: Path | None, module: str) -> str | None:
    """登记时确认缓存仍是该来源的解析结果；映射只可补充空白检查键和范围。"""
    if source is None:
        if candidates_file is not None:
            cached = json.loads(candidates_file.read_text(encoding="utf-8"))
            # 空候选不包含任何可写入记忆的事实；允许记录解析提示的空基线，
            # 但含有记录时必须提供原始来源，防止编辑候选绕过来源校验。
            if isinstance(cached, dict) and cached.get("records") == []:
                return None
        return "登记台账必须同时提供原始来源 --source；缓存候选仅可用于预览"
    parsed = parse_source(source, module)
    if candidates_file is None:
        return None
    cached = json.loads(candidates_file.read_text(encoding="utf-8"))
    if (not isinstance(cached, dict) or cached.get("schema") != parsed["schema"]
            or cached.get("module") != module
            or cached.get("source_file") != parsed["source_file"]
            or cached.get("source_sha256") != parsed["source_sha256"]
            or cached.get("issues") != parsed["issues"]
            or cached.get("coverage") != parsed["coverage"]
            or not isinstance(cached.get("records"), list)
            or len(cached["records"]) != len(parsed["records"])):
        return "缓存候选与原始来源元数据不一致，请重新解析"
    mapped_fields = {"check_order", "research_id", "scope"}
    for original, candidate in zip(parsed["records"], cached["records"]):
        if not isinstance(candidate, dict):
            return "缓存候选与原始来源内容不一致，请重新解析"
        expected = dict(original)
        for field in mapped_fields:
            if not expected.get(field) and candidate.get(field):
                expected[field] = candidate[field]
        if candidate != expected:
            return "缓存候选与原始来源内容不一致，请重新解析"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--module", required=True)
    parser.add_argument("--source", type=Path, help="来源文件（与 --candidates 二选一）")
    parser.add_argument("--candidates", type=Path, help="已解析的候选 JSON，避免重复解析")
    parser.add_argument("--map", type=Path, help="QA 问题到矩阵检查键的映射 JSON（助手生成、用户确认）")
    parser.add_argument("--scope", default="", help="来源未提供适用条件时的默认范围（公司/账套/期间）")
    parser.add_argument("--record", action="store_true", help="写入完成后回读校验并登记导入台账")
    args = parser.parse_args()

    module = validate_module(args.module)
    if not (args.source or args.candidates):
        parser.error("需要 --source 或 --candidates")
    metadata = {}
    candidates, source_name, source_sha256 = _candidates_from(
        args.source, args.candidates, module, metadata=metadata)
    mapping = []
    if args.map is not None:
        data = json.loads(args.map.read_text(encoding="utf-8"))
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise ValueError("映射文件必须是对象列表：[{question, check_order, research_id, scope?}]")
        mapping = data
    candidates, unmatched = apply_mapping(candidates, mapping) if mapping else (candidates, [])

    if args.record:
        integrity_problem = _candidate_integrity(args.source, args.candidates, module)
        if integrity_problem:
            result = {"recorded": False, "problems": [{"reason": integrity_problem}]}
        elif metadata["problems"]:
            result = {"recorded": False, "problems": metadata["problems"]}
        elif unmatched:
            result = {"recorded": False, "problems":
                      [{"question": (row.get("locator") or {}).get("question", ""),
                        "reason": "QA 未映射矩阵检查键，未写入，不能登记台账"}
                       for row in unmatched]}
        else:
            result = record_import(args.workspace, module, source_name, source_sha256,
                                   candidates, default_scope=args.scope)
        print(json.dumps(result, ensure_ascii=False))
        return

    buckets = classify(args.workspace, module, candidates, default_scope=args.scope)
    for problem in metadata["problems"]:
        buckets["blocked"].append({"check_order": "", "research_id": "",
                                   "reasons": [problem["reason"]], **problem})
    for row in unmatched:
        buckets["blocked"].append(
            {"check_order": "", "research_id": "", "config_item": row.get("config_item") or "",
             "locator": row.get("locator"), "conclusion": row.get("conclusion") or "",
             "reasons": ["QA 未映射矩阵检查键：由助手生成 --map 映射并经用户确认"]})
    removed = removed_in_source(args.workspace, module, source_name, candidates, default_scope=args.scope)
    previous = _latest_entry(load_ledger(args.workspace, module), source_name)
    delta = bool(buckets["new"] or buckets["changed"] or removed or buckets["blocked"])
    verdict = "delta" if delta else "no-op"
    notes = [SOURCE_HINTS[issue["code"]] for issue in metadata["issues"]
             if issue.get("code") in SOURCE_HINTS]
    if unmatched:
        notes.append(f"{len(unmatched)} 个 QA 问题尚未映射矩阵检查键，全部阻塞，不得猜测检查键")
    if previous and previous.get("source_sha256") == source_sha256 and delta:
        notes.append("来源文件与上次导入相同但存在差异，可能记录被修复或手工修订，按差异执行")
    if not previous:
        notes.append("该来源首次导入：new 项逐条经用户确认后用 dy_research_record_write 写入")
    print(json.dumps({"schema": "dy-research-incremental/v1", "module": module,
                      "source_name": source_name, "source_sha256": source_sha256,
                      "verdict": verdict, "notes": notes,
                      "counts": {name: len(items) for name, items in buckets.items()}
                      | {"removed_in_source": len(removed)},
                      **buckets, "removed_in_source": removed}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
