"""读取随插件发布的模块调研模板，不依赖原工作簿。"""

import json
from pathlib import Path


def load_template(module: str) -> dict:
    """返回当前模块模板的独立副本；未知模块不使用默认模板。"""
    if module not in ("expense", "general-ledger"):
        raise ValueError(f"未知调研模板模块：{module}")
    path = Path(__file__).parent / "data" / "research_templates" / f"{module}.json"
    return json.loads(path.read_text(encoding="utf-8"))
