"""在隔离用户目录中验证 Codex 市场安装，避免复用开发环境。"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which('codex') is None, reason='需要本机 Codex CLI')
def test_codex_installs_plugin_from_clean_marketplace(tmp_path):
    root = Path(__file__).parents[1]
    marketplace = tmp_path / 'marketplace'
    catalog = marketplace / '.agents/plugins'
    catalog.mkdir(parents=True)
    shutil.copy2(root / '.agents/plugins/marketplace.json', catalog)
    shutil.copytree(root / 'plugins/dy_superpowers', marketplace / 'plugins/dy_superpowers',
                    ignore=shutil.ignore_patterns('.venv', '.pytest_cache', '__pycache__', '*.egg-info'))
    env = {**os.environ, 'CODEX_HOME': str(tmp_path / 'codex')}
    (tmp_path / 'codex').mkdir()

    def run(*args):
        result = subprocess.run(['codex', 'plugin', *args, '--json'], env=env,
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    added = run('marketplace', 'add', str(marketplace))
    assert added['marketplaceName'] == 'dy-superpowers'
    installed = run('add', 'dy_superpowers@dy-superpowers')
    plugin = Path(installed['installedPath'])
    assert (plugin / '.mcp.json').is_file()
    assert (plugin / 'mcp-server/uv.lock').is_file()
    assert not (plugin / 'mcp-server/.venv').exists()
    inventory = run('list', '--marketplace', 'dy-superpowers')
    assert any(item['name'] == 'dy_superpowers' and item['installed'] and item['enabled']
               for item in inventory['installed'])
