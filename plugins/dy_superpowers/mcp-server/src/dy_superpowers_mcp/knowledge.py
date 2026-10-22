"""HRP/Odoo 项目的本地模块知识索引；知识内容维护在 data/modules.json。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MODULES: dict[str, dict[str, Any]] = json.loads((Path(__file__).parent / "data" / "modules.json").read_text(encoding="utf-8"))


def module_info(module: str) -> dict[str, Any]:
    info = MODULES.get(module)
    return {"found": False, "module": module, "available": sorted(MODULES)} if info is None else {"found": True, "module": module, **info}


def search(query: str) -> dict[str, Any]:
    query = query.strip().lower()
    results = []
    for key, info in MODULES.items():
        haystack = " ".join([key, info["name"], *info["aliases"], *info["models"]]).lower()
        if query and query in haystack:
            results.append({"module": key, "name": info["name"], "models": info["models"]})
    return {"query": query, "results": results, "count": len(results)}
