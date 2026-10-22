"""本地模块知识以数据文件维护，代码只负责加载和检索。"""
import json
from pathlib import Path

from dy_superpowers_mcp import knowledge

DATA_FILE = Path(knowledge.__file__).parent / "data" / "modules.json"


def test_module_knowledge_is_loaded_from_data_file():
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    assert data == knowledge.MODULES
    assert {"account", "hr_expense", "hr", "purchase"} <= set(data)


def test_module_knowledge_entries_follow_schema():
    for key, info in knowledge.MODULES.items():
        assert key == key.lower() and " " not in key
        assert set(info) == {"name", "aliases", "models", "workflow", "depends"}
        assert isinstance(info["name"], str) and info["name"]
        assert isinstance(info["workflow"], str) and info["workflow"]
        for field in ("aliases", "models", "depends"):
            assert isinstance(info[field], list) and all(isinstance(item, str) and item for item in info[field])
        assert all(dependency == "base" or dependency in knowledge.MODULES for dependency in info["depends"])


def test_knowledge_search_and_info_behaviour_is_unchanged():
    assert knowledge.module_info("account")["found"] is True
    assert knowledge.module_info("missing") == {"found": False, "module": "missing", "available": sorted(knowledge.MODULES)}
    assert [item["module"] for item in knowledge.search("报销")["results"]] == ["hr_expense"]
