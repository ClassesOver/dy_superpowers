"""插件安装清单的遥测边界。"""

import json
from pathlib import Path


def test_mcp_install_config_enables_plugin_only_telemetry_by_default():
    plugin = Path(__file__).parents[1] / "plugins/dy_superpowers"
    config = json.loads((plugin / ".mcp.json").read_text(encoding="utf-8"))
    server = config["mcpServers"]["dy-superpowers-odoo"]
    assert server["env"] == {
        "DY_SUPERPOWERS_OTLP_TRACES_ENDPOINT": "http://127.0.0.1:33049/v1/traces",
        "DY_SUPERPOWERS_TELEMETRY_SCOPE": "plugin",
    }


def test_plugin_telemetry_has_a_dedicated_service_resource():
    plugin = Path(__file__).parents[1] / "plugins/dy_superpowers"
    source = (plugin / "mcp-server/src/dy_superpowers_mcp/telemetry.py").read_text(encoding="utf-8")
    assert '"service.name": PLUGIN_SERVICE_NAME' in source
    assert '"dy.plugin": PLUGIN_SERVICE_NAME' in source
