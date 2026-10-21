import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
CATALOG = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))

def test_catalog_points_to_a_valid_plugin_manifest():
    assert CATALOG["name"] == "dy-superpowers"
    assert len(CATALOG["plugins"]) == 1
    entry = CATALOG["plugins"][0]
    assert entry["name"] == "dy_superpowers"
    assert entry["source"]["source"] == "local"
    plugin = ROOT / entry["source"]["path"].removeprefix("./")
    manifest = json.loads((plugin / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == entry["name"]
    assert manifest["mcpServers"] == "./.mcp.json"
    assert (plugin / ".mcp.json").is_file()
    assert (plugin / "skills").is_dir()

def test_marketplace_policy_is_complete():
    policy = CATALOG["plugins"][0]["policy"]
    assert policy["installation"] in {"NOT_AVAILABLE", "AVAILABLE", "INSTALLED_BY_DEFAULT"}
    assert policy["authentication"] in {"ON_INSTALL", "ON_USE"}
    assert CATALOG["plugins"][0]["category"]
