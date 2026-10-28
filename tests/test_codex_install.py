"""在隔离用户目录中验证 Codex 市场安装，避免复用开发环境。"""

import json
import queue
import threading
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
    effective = subprocess.run(['codex', 'mcp', 'get', 'dy-superpowers-odoo', '--json'],
                               env=env, capture_output=True, text=True, timeout=30)
    assert effective.returncode == 0, effective.stderr
    transport = json.loads(effective.stdout)['transport']
    workspace = tmp_path / 'business-workspace'
    workspace.mkdir()
    env['DY_WORKSPACE_ROOT'] = str(workspace)
    process = subprocess.Popen(['codex', 'app-server', '--stdio'], env=env, cwd=workspace,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    messages = queue.Queue()
    errors = []
    def read_errors():
        for line in process.stderr:
            errors.append(line)
    threading.Thread(target=read_errors, daemon=True).start()
    def read():
        for line in process.stdout:
            messages.put(json.loads(line))
    threading.Thread(target=read, daemon=True).start()
    def request(identifier, method, params):
        process.stdin.write(json.dumps({'id': identifier, 'method': method, 'params': params}) + '\n')
        process.stdin.flush()
        while True:
            message = messages.get(timeout=90)
            if message.get('id') == identifier:
                assert 'error' not in message, message.get('error')
                return message['result']
    try:
        request(1, 'initialize', {'clientInfo': {'name': 'plugin-install-test', 'version': '1'}})
        process.stdin.write(json.dumps({'method': 'initialized'}) + '\n')
        process.stdin.flush()
        status = request(2, 'mcpServerStatus/list', {'serverName': 'dy-superpowers-odoo'})
        entry = next(item for item in status['data'] if item['name'] == 'dy-superpowers-odoo')
        assert 'dy_research_progress' in entry['tools'], str(entry.get('toolsError')) + '\n' + ''.join(errors[-10:])
    finally:
        process.terminate()
        process.communicate(timeout=10)


@pytest.mark.skipif(shutil.which('codex') is None, reason='需要本机 Codex CLI')
def test_codex_loads_frontmatter_with_its_own_scalar_and_duplicate_rules(tmp_path):
    codex_home = tmp_path / 'codex'
    skills_root = codex_home / 'skills'
    skills_root.mkdir(parents=True)
    fixtures = {
        'boolean-scalar': 'name: on\ndescription: UNIQUE_VALID_BOOLEAN_SCALAR\n',
        'date-scalar': 'name: 2026-10-21\ndescription: UNIQUE_VALID_DATE_SCALAR\n',
        'duplicate-name': 'name: first\nname: second\ndescription: UNIQUE_DUPLICATE_NAME\n',
        'duplicate-description': (
            'name: duplicate-description\ndescription: first\n'
            'description: UNIQUE_DUPLICATE_DESCRIPTION\n'
        ),
    }
    for directory, frontmatter in fixtures.items():
        skill_dir = skills_root / directory
        skill_dir.mkdir()
        (skill_dir / 'SKILL.md').write_text(
            f'---\n{frontmatter}---\n# Fixture skill\n', encoding='utf-8')

    home = tmp_path / 'home'
    home.mkdir()
    env = {**os.environ, 'CODEX_HOME': str(codex_home), 'HOME': str(home)}
    result = subprocess.run(
        ['codex', 'debug', 'prompt-input'], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    prompt = json.dumps(json.loads(result.stdout), ensure_ascii=False)

    assert 'UNIQUE_VALID_BOOLEAN_SCALAR' in prompt
    assert 'UNIQUE_VALID_DATE_SCALAR' in prompt
    assert 'UNIQUE_DUPLICATE_NAME' not in prompt
    assert 'UNIQUE_DUPLICATE_DESCRIPTION' not in prompt
