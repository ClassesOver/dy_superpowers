"""读取随插件发布的模块调研模板，不依赖原工作簿。"""

import json
from functools import cache
from pathlib import Path

TEMPLATE_DIR = Path(__file__).parent / "data" / "research_templates"


@cache
def template_modules() -> tuple[str, ...]:
    """已发布模板的模块标识；模板目录缺失或为空视为安装损坏，立即失败而非返回空清单。"""
    modules = tuple(sorted(path.stem for path in TEMPLATE_DIR.glob("*.json")))
    if not modules:
        raise RuntimeError(f"调研模板未随插件打包：{TEMPLATE_DIR} 下没有模板文件，请重新安装插件")
    return modules


def load_template(module: str) -> dict:
    """返回当前模块模板的独立副本；未知模块不使用默认模板。"""
    if module not in template_modules():
        raise ValueError(f"未知调研模板模块：{module}")
    return json.loads((TEMPLATE_DIR / f"{module}.json").read_text(encoding="utf-8"))
