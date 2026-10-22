import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).parents[1]
CATALOG = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))

INVALID_SKILL_METADATA = {
    "missing_frontmatter": "# 模块调研\n",
    "unclosed_frontmatter": "---\ndescription: 模块调研\n",
    "invalid_yaml": "---\ndescription: 模块调研\nmetadata:\n  child: valid\n   bad: indent\n---\n",
    "missing_description": "---\nname: module-research\n---\n",
    "empty_description": "---\ndescription: '  '\n---\n",
    "invalid_description": "---\ndescription: [模块调研]\n---\n",
    "invalid_name": "---\nname: [module-research]\ndescription: 模块调研\n---\n",
    "long_name": "---\nname: " + "a" * 65 + "\ndescription: 模块调研\n---\n",
    "invalid_metadata": "---\ndescription: 模块调研\nmetadata: []\n---\n",
    "invalid_short_description": "---\ndescription: 模块调研\nmetadata:\n  short-description: [调研]\n---\n",
}


class _CodexFrontmatterLoader(yaml.SafeLoader):
    yaml_implicit_resolvers = {
        first_character: [
            (tag, resolver)
            for tag, resolver in resolvers
            if tag not in {
                "tag:yaml.org,2002:bool",
                "tag:yaml.org,2002:float",
                "tag:yaml.org,2002:int",
                "tag:yaml.org,2002:timestamp",
            }
        ]
        for first_character, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }


def _assert_no_duplicate_codex_fields(node):
    if not isinstance(node, yaml.MappingNode):
        return
    fields = {}
    for key_node, value_node in node.value:
        if isinstance(key_node, yaml.ScalarNode) and key_node.value in {"name", "description", "metadata"}:
            key = key_node.value
            assert key not in fields, f"技能元数据存在重复字段：{key}"
            fields[key] = value_node

    metadata = fields.get("metadata")
    if isinstance(metadata, yaml.MappingNode):
        found_short_description = False
        for key_node, _ in metadata.value:
            if isinstance(key_node, yaml.ScalarNode) and key_node.value == "short-description":
                assert not found_short_description, "技能元数据存在重复字段：short-description"
                found_short_description = True


def _local_reference(plugin, value, *, directory=False):
    assert isinstance(value, str) and value.startswith("./"), "插件引用必须是 ./ 开头的相对路径"
    path = (plugin / value).resolve()
    assert path.is_relative_to(plugin.resolve()), "插件引用不得越出插件目录"
    exists = path.exists() if directory is None else (path.is_dir() if directory else path.is_file())
    assert exists, f"插件引用不存在或类型错误：{value}"
    return path


def _load_codex_frontmatter(frontmatter):
    node = yaml.compose(frontmatter, Loader=_CodexFrontmatterLoader)
    _assert_no_duplicate_codex_fields(node)
    return yaml.load(frontmatter, Loader=_CodexFrontmatterLoader)


def _repair_codex_frontmatter_scalars(frontmatter):
    repaired = []
    block_scalar_indent = None
    changed = False
    for line in frontmatter.splitlines():
        indent = len(line) - len(line.lstrip(" "))
        if block_scalar_indent is not None:
            if not line.strip() or indent > block_scalar_indent:
                repaired.append(line)
                continue
            block_scalar_indent = None

        if ":" not in line:
            repaired.append(line)
            continue
        key, value = line.split(":", 1)
        if not key.strip() or (value and not value[0].isspace()):
            repaired.append(line)
            continue

        trimmed = value.lstrip()
        leading = value[:len(value) - len(trimmed)]
        scalar = trimmed
        comment = ""
        for index, character in enumerate(trimmed):
            if character == "#" and (index == 0 or trimmed[index - 1].isspace()):
                comment_start = len(trimmed[:index].rstrip())
                scalar, comment = trimmed[:comment_start], trimmed[comment_start:]
                break

        scalar = scalar.rstrip()
        if not scalar:
            repaired.append(line)
            continue
        if scalar[0] in "|>":
            block_scalar_indent = indent
            repaired.append(line)
            continue
        if scalar[0] in "'\"":
            repaired.append(line)
            continue

        has_colon_separator = any(
            character == ":" and index + 1 < len(scalar) and scalar[index + 1].isspace()
            for index, character in enumerate(scalar)
        )
        invalid_flow_like = scalar[0] in "[{@`"
        if invalid_flow_like:
            try:
                yaml.safe_load(scalar)
            except yaml.YAMLError:
                pass
            else:
                invalid_flow_like = False
        if not has_colon_separator and not invalid_flow_like:
            repaired.append(line)
            continue

        quoted = "'" + scalar.replace("'", "''") + "'"
        repaired.append(f"{key}:{leading}{quoted}{comment}")
        changed = True
    return "\n".join(repaired) if changed else None


def _validate_skill_metadata(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines and lines[0].strip() == "---", f"{path} 缺少 YAML 元数据"
    closing = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    assert closing is not None, f"{path} 的 YAML 元数据缺少结束分隔符"
    frontmatter = "\n".join(lines[1:closing])
    try:
        metadata = _load_codex_frontmatter(frontmatter)
    except yaml.YAMLError as original_error:
        repaired = _repair_codex_frontmatter_scalars(frontmatter)
        if repaired is None:
            raise AssertionError(f"{path} 的 YAML 元数据无效") from original_error
        try:
            metadata = _load_codex_frontmatter(repaired)
        except yaml.YAMLError as exc:
            raise AssertionError(f"{path} 的 YAML 元数据无效") from exc
    assert isinstance(metadata, dict), f"{path} 的 YAML 元数据必须是对象"
    description = metadata.get("description")
    assert isinstance(description, str) and description.strip(), f"{path} 缺少非空文本 description"
    name = metadata.get("name")
    assert name is None or isinstance(name, str), f"{path} 的 name 必须是文本"
    name = " ".join((name or "").split()) or path.parent.name
    assert len(name) <= 64, f"{path} 的技能名称超过 64 个字符"
    extra = metadata.get("metadata", {})
    assert isinstance(extra, dict), f"{path} 的 metadata 必须是对象"
    short_description = extra.get("short-description")
    assert short_description is None or isinstance(short_description, str), f"{path} 的 short-description 必须是文本"


def test_catalog_points_to_a_valid_plugin_manifest():
    assert CATALOG["name"] == "dy-superpowers"
    assert len(CATALOG["plugins"]) == 1
    entry = CATALOG["plugins"][0]
    assert entry["name"] == "dy_superpowers"
    assert entry["source"]["source"] == "local"
    plugin = _local_reference(ROOT, entry["source"]["path"], directory=True)
    manifest = json.loads((plugin / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == entry["name"]
    assert plugin.name == manifest["name"]
    assert manifest["mcpServers"] == "./.mcp.json"
    skills = _local_reference(plugin, manifest.get("skills"), directory=True)
    skill_directories = [path for path in skills.iterdir() if path.is_dir()]
    assert skill_directories, "技能目录不能为空"
    for skill in skill_directories:
        path = _local_reference(plugin, "./" + str(skill.relative_to(plugin) / "SKILL.md"))
        _validate_skill_metadata(path)

    mcp = json.loads(_local_reference(plugin, manifest.get("mcpServers")).read_text(encoding="utf-8"))
    assert isinstance(mcp, dict) and isinstance(mcp.get("mcpServers"), dict), "MCP 配置必须包含 mcpServers 对象"
    servers = mcp["mcpServers"]
    assert servers, "MCP 服务不能为空"
    for config in servers.values():
        assert isinstance(config, dict), "MCP 服务配置必须是对象"
        assert isinstance(config.get("command"), str) and config["command"].strip(), "MCP 缺少启动命令"
        assert isinstance(config.get("args"), list) and all(isinstance(arg, str) for arg in config["args"]), "MCP args 必须是字符串数组"
        for arg in config["args"]:
            if arg.startswith("${PLUGIN_ROOT}/"):
                value = "./" + arg.removeprefix("${PLUGIN_ROOT}/")
                _local_reference(plugin, value, directory=None)

    hooks = json.loads(_local_reference(plugin, manifest.get("hooks")).read_text(encoding="utf-8"))
    assert isinstance(hooks, dict) and isinstance(hooks.get("hooks"), dict), "hooks 配置必须包含 hooks 对象"
    groups = hooks["hooks"].get("SessionStart")
    assert isinstance(groups, list) and groups, "缺少 SessionStart hook"
    for group in groups:
        assert isinstance(group, dict) and isinstance(group.get("hooks"), list) and group["hooks"], "hook 分组无效"
        for hook in group["hooks"]:
            assert isinstance(hook, dict) and hook.get("type") == "mcp_tool", "SessionStart 必须调用 MCP 工具"
            assert isinstance(hook.get("server"), str) and hook["server"] in servers, "hook 引用了未注册的 MCP 服务"
            assert isinstance(hook.get("tool"), str) and hook["tool"].strip(), "hook 缺少工具名"
            assert isinstance(hook.get("input"), dict), "hook input 必须是对象"

def test_marketplace_policy_is_complete():
    policy = CATALOG["plugins"][0]["policy"]
    assert policy["installation"] in {"NOT_AVAILABLE", "AVAILABLE", "INSTALLED_BY_DEFAULT"}
    assert policy["authentication"] in {"ON_INSTALL", "ON_USE"}
    assert CATALOG["plugins"][0]["category"]


@pytest.mark.parametrize("damage", [
    "mcp_json", "hooks_json", "missing_skills", "missing_skill_file",
    "missing_mcp_command", "unknown_hook_server", "outside_plugin_path",
    *INVALID_SKILL_METADATA,
])
def test_layout_rejects_broken_plugin_references(tmp_path, monkeypatch, damage):
    plugin = tmp_path / "plugins/dy_superpowers"
    shutil.copytree(ROOT / "plugins/dy_superpowers", plugin,
                    ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", "*.egg-info"))
    manifest_path = plugin / ".codex-plugin/plugin.json"
    manifest = json.loads(manifest_path.read_text())
    if damage in {"mcp_json", "hooks_json"}:
        target = ".mcp.json" if damage == "mcp_json" else "hooks/hooks.json"
        (plugin / target).write_text("{invalid", encoding="utf-8")
    elif damage in {"missing_skills", "outside_plugin_path"}:
        manifest["skills"] = "./missing/" if damage == "missing_skills" else "../outside/"
        (plugin.parent / "outside").mkdir()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif damage == "missing_skill_file":
        (plugin / "skills/module-research/SKILL.md").unlink()
    elif damage in INVALID_SKILL_METADATA:
        (plugin / "skills/module-research/SKILL.md").write_text(INVALID_SKILL_METADATA[damage], encoding="utf-8")
    elif damage == "missing_mcp_command":
        path = plugin / ".mcp.json"
        config = json.loads(path.read_text())
        del config["mcpServers"]["dy-superpowers-odoo"]["command"]
        path.write_text(json.dumps(config), encoding="utf-8")
    else:
        path = plugin / "hooks/hooks.json"
        config = json.loads(path.read_text())
        config["hooks"]["SessionStart"][0]["hooks"][0]["server"] = "not-registered"
        path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)

    with pytest.raises((AssertionError, json.JSONDecodeError)):
        test_catalog_points_to_a_valid_plugin_manifest()


@pytest.mark.parametrize('name', ['', "name: ''\n"])
def test_layout_accepts_default_skill_name_and_multiline_description(tmp_path, monkeypatch, name):
    plugin = tmp_path / 'plugins/dy_superpowers'
    shutil.copytree(ROOT / 'plugins/dy_superpowers', plugin,
                    ignore=shutil.ignore_patterns('.venv', '__pycache__', '.pytest_cache', '*.egg-info'))
    (plugin / 'skills/module-research/SKILL.md').write_text(
        '---\n' + name + 'description: >\n  模块调研，\n  支持多轮问答。\n'
        'metadata:\n  short-description: 调研\n---\n# 模块调研\n', encoding='utf-8')
    monkeypatch.setattr(sys.modules[__name__], 'ROOT', tmp_path)
    test_catalog_points_to_a_valid_plugin_manifest()


@pytest.mark.parametrize('frontmatter', [
    'description: Build for AWS: ECS\n',
    'name: unknown\ndescription: Unknown fields\n'
    'argument-hint: <duration: e.g. 7d, 2w>\ntags: [next,@supabase/ssr]\n',
    'name: block\ndescription: |\n  Build for AWS: ECS\nargument-hint: <duration: e.g. 7d>\n',
])
def test_layout_accepts_codex_repaired_frontmatter(tmp_path, frontmatter):
    path = tmp_path / 'skill/SKILL.md'
    path.parent.mkdir()
    path.write_text('---\n' + frontmatter + '---\n', encoding='utf-8')
    _validate_skill_metadata(path)


@pytest.mark.parametrize('frontmatter', [
    'name: first\nname: second\ndescription: Unique description\n',
    'name: probe\ndescription: first\ndescription: second\n',
    'name: probe\ndescription: Unique description\nmetadata: {}\nmetadata: {}\n',
    'name: probe\ndescription: Unique description\nmetadata:\n'
    '  short-description: first\n  short-description: second\n',
])
def test_layout_rejects_duplicate_supported_frontmatter_fields(tmp_path, frontmatter):
    path = tmp_path / 'skill/SKILL.md'
    path.parent.mkdir()
    path.write_text('---\n' + frontmatter + '---\n', encoding='utf-8')

    with pytest.raises(AssertionError, match='重复字段'):
        _validate_skill_metadata(path)


@pytest.mark.parametrize('frontmatter', [
    'name: on\ndescription: Valid description\n',
    'name: 2026-10-21\ndescription: Valid description\n',
    'name: 123\ndescription: 456\n',
    'name: 1.25\ndescription: 3.14\n',
    'name: probe\ndescription: 2026-10-21\n'
    'metadata:\n  short-description: yes\n',
    'name: null\ndescription: Valid description\nmetadata:\n'
    '  short-description: null\n',
])
def test_layout_treats_codex_string_scalars_as_text(tmp_path, frontmatter):
    path = tmp_path / 'skill/SKILL.md'
    path.parent.mkdir()
    path.write_text('---\n' + frontmatter + '---\n', encoding='utf-8')

    _validate_skill_metadata(path)


def test_layout_rejects_null_description(tmp_path):
    path = tmp_path / 'skill/SKILL.md'
    path.parent.mkdir()
    path.write_text('---\nname: probe\ndescription: null\n---\n', encoding='utf-8')

    with pytest.raises(AssertionError, match='description'):
        _validate_skill_metadata(path)


def test_layout_ignores_duplicate_unsupported_fields_like_codex(tmp_path):
    path = tmp_path / 'skill/SKILL.md'
    path.parent.mkdir()
    path.write_text(
        '---\nname: probe\ndescription: Valid description\n'
        'extension: first\nextension: second\n---\n',
        encoding='utf-8',
    )

    _validate_skill_metadata(path)
