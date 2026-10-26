"""项目身份与本地 Wiki 构建/发布清单完整性回归。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import server
from dy_superpowers_mcp.memory import write_note
from dy_superpowers_mcp.project_wiki import (
    build_bundle, configure_project, load_project, load_staged, owner_marker, safe_path, stage_wiki,
)
from dy_superpowers_mcp.research import write_record

PROJECT = {"name": "示例医院一体化交付项目", "code": "demo-a"}


def _record(**overrides):
    base = {"module": "expense", "check_order": "1", "research_id": "01",
            "config_item": "应用安装", "conclusion": "费用基础应用需要安装并启用。",
            "scope": "示例医院/2026 上线", "confirmed_by": "业务负责人",
            "confirmed_at": "2026-10-20", "confirmed": True}
    base.update(overrides)
    return base


def _workspace(tmp_path, records=None, name=PROJECT["name"], code=PROJECT["code"]):
    workspace = tmp_path / f"ws-{code}"
    workspace.mkdir()
    configure_project(workspace, name, code, confirmed=True)
    for record in (records if records is not None else [_record()]):
        write_record(workspace, record)
    return workspace


def _server_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(server, "initialize_telemetry", lambda: False)


def run(tool, args, ws):
    return server.execute_tool(tool, {"trace_id": "project-wiki", **args}, ws)


def test_configure_requires_user_confirmation(tmp_path):
    with pytest.raises(PermissionError):
        configure_project(tmp_path, PROJECT["name"], PROJECT["code"])
    assert not (tmp_path / ".dy_superpowers/project.json").exists()


def test_configure_validates_name_and_code(tmp_path):
    for bad_name in ("", "  ", "x" * 101, "带\n换行"):
        with pytest.raises(ValueError, match="项目名称"):
            configure_project(tmp_path, bad_name, "demo-a", confirmed=True)
    for bad_code in ("DemoA", "-demo", "demo-", "d" * 33, "示例"):
        with pytest.raises(ValueError, match="项目代号"):
            configure_project(tmp_path, PROJECT["name"], bad_code, confirmed=True)
    assert not (tmp_path / ".dy_superpowers/project.json").exists()


def test_project_identity_is_stable_and_rename_refused(tmp_path):
    first = configure_project(tmp_path, PROJECT["name"], PROJECT["code"], confirmed=True)
    again = configure_project(tmp_path, " " + PROJECT["name"] + " ", PROJECT["code"], confirmed=True)
    assert again["project_id"] == first["project_id"]
    with pytest.raises(ValueError, match="不能改名"):
        configure_project(tmp_path, "另一个项目", PROJECT["code"], confirmed=True)
    with pytest.raises(ValueError, match="不能改名"):
        configure_project(tmp_path, PROJECT["name"], "other-code", confirmed=True)


def test_legacy_guessed_config_is_rejected_not_upgraded(tmp_path):
    legacy = tmp_path / ".dy_superpowers"
    legacy.mkdir(parents=True)
    (legacy / "project.json").write_text(
        json.dumps({"name": "vali5 测试账套", "code": "vali5"}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="禁止猜测"):
        load_project(tmp_path)
    with pytest.raises(ValueError):
        build_bundle(tmp_path)
    assert json.loads((legacy / "project.json").read_text(encoding="utf-8"))["name"] == "vali5 测试账套"


def test_bundle_navigation_and_stable_topic_slugs(tmp_path):
    workspace = _workspace(tmp_path, [
        _record(research_id="01"), _record(research_id="02"),
        _record(check_order="2", research_id="01", config_item="费用政策"),
        _record(check_order="2", research_id="01", config_item="费用政策", scope="示例医院/2027",
                confirmed_by="用户授权导入调研工作簿"),
        _record(check_order="3", research_id="01", config_item="预算控制", status="retired",
                retirement_reason="本期不启用预算控制"),
    ])
    bundle = build_bundle(workspace)
    prefix = PROJECT["code"] + "-" + bundle["project"]["project_id"].replace("-", "")[:8]
    overview = bundle["overview_slug"]
    assert overview == f"{prefix}-overview" and overview in bundle["pages"]
    topics = {slug for slug in bundle["pages"] if "-topic-" in slug}
    assert len(topics) == 3  # check_order 1 / 2 / 3 各一个主题页
    assert f"{prefix}-expense-index" in bundle["pages"]

    page_texts = "\n".join(bundle["pages"].values())
    for slug, content in bundle["pages"].items():
        assert content.startswith(owner_marker(bundle["project"]["project_id"], slug))
        assert not content.startswith("---\n")  # 不输出 YAML frontmatter
    assert "唯一项目代号：demo-a" in bundle["pages"][overview]
    assert "模块导航" in bundle["pages"][overview] and "费用管理" in bundle["pages"][overview]
    # 目录表统一两列：第一列本身即跳转链接，不单设“阅读/入口/查看”列。
    index_page = bundle["pages"][f"{prefix}-expense-index"]
    assert index_page.count("| --- |") == 1 and "| 阅读 |" not in index_page
    assert "[应用安装](" in index_page and "[应用安装](overview)" not in index_page
    assert "| 入口 |" not in bundle["pages"][overview] and "[费用管理](" in bundle["pages"][overview]
    assert "已停用" in page_texts and "来源记录，业务签认待核实" in page_texts
    assert "已记录，业务适用性须复核" in page_texts

    before = build_bundle(workspace)
    slug_before = {slug for slug in before["pages"] if "-topic-" in slug}
    write_record(workspace, _record(check_order="10", research_id="01", config_item="单据类型"))
    after = build_bundle(workspace)
    slug_after = {slug for slug in after["pages"] if "-topic-" in slug}
    assert slug_before <= slug_after and len(slug_after) == len(slug_before) + 1


def test_sources_registry_binding_and_link_rules(tmp_path):
    workspace = _workspace(tmp_path)
    bundle = build_bundle(workspace)
    overview = bundle["pages"][bundle["overview_slug"]]
    assert "尚未登记原始文件链接" in overview

    sources = workspace / ".dy_superpowers/sources.json"
    sources.write_text(json.dumps([{"project_id": "其他项目", "name": "费用调研.xlsx",
                                    "url": "https://gitlab.example.com/superpowers/research/-/blob/main/sources/a.xlsx"}]),
                       encoding="utf-8")
    with pytest.raises(ValueError, match="归属不匹配"):
        build_bundle(workspace)
    sources.write_text(json.dumps([{"project_id": bundle["project"]["project_id"], "name": "费用调研.xlsx",
                                    "url": "https://user:pat@gitlab.example.com/a.xlsx"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="无凭据"):
        build_bundle(workspace)
    sources.write_text(json.dumps([{"project_id": bundle["project"]["project_id"], "name": "费用调研.xlsx",
                                    "url": "https://gitlab.example.com/superpowers/research/-/blob/main/sources/expense.xlsx"}]),
                       encoding="utf-8")
    overview = build_bundle(workspace)["pages"][bundle["overview_slug"]]
    # md() 转义文件名中的点；GitLab 渲染后仍显示为原文件名。
    assert "[费用调研\\.xlsx](https://gitlab.example.com/superpowers/research/-/blob/main/sources/expense.xlsx)" in overview


def test_stage_and_load_staged_integrity(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    stage_wiki(workspace)
    manifest, pages = load_staged(workspace)
    assert {entry["slug"] for entry in manifest["entries"]} == set(pages)
    assert manifest["overview_slug"] in pages
    write_note(workspace, "legacy-note", "旧笔记", "旧 notes 不是发布输入", [], confirmed=True)
    assert "legacy-note" not in {slug for slug in pages}
    extra = workspace / ".dy_superpowers/wiki/pages" / "not-in-manifest.md"
    extra.write_text("越界页", encoding="utf-8")
    manifest, pages = load_staged(workspace)
    assert "not-in-manifest" not in pages  # 清单是白名单

    first_slug = manifest["entries"][0]["slug"]
    page_file = workspace / ".dy_superpowers/wiki/pages" / (first_slug + ".md")
    page_file.write_text("被篡改", encoding="utf-8")
    with pytest.raises(ValueError, match="已变更"):
        load_staged(workspace)
    page_file.unlink()
    with pytest.raises(OSError):
        load_staged(workspace)
    stage_wiki(workspace)  # 重建恢复

    manifest_path = workspace / ".dy_superpowers/wiki/manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["entries"][0]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="已变更"):
        load_staged(workspace)
    data["entries"][0]["slug"] = "evil-page"
    manifest_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="越界或重复"):
        load_staged(workspace)


def test_staged_manifest_rejects_research_changes(tmp_path):
    workspace = _workspace(tmp_path)
    stage_wiki(workspace)
    load_staged(workspace)
    write_record(workspace, _record(check_order="2", research_id="01", config_item="费用政策"))
    with pytest.raises(ValueError, match="构建后发生变化"):
        load_staged(workspace)
    stage_wiki(workspace)
    manifest, pages = load_staged(workspace)
    assert any("-topic-" in slug for slug in pages)


def test_safe_path_rejects_escape_and_symlink(tmp_path):
    with pytest.raises(ValueError):
        safe_path(tmp_path, "..", "..", "secret")
    (tmp_path / ".dy_superpowers").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".dy_superpowers/wiki").symlink_to(outside)
    with pytest.raises(ValueError, match="符号链接"):
        safe_path(tmp_path, "wiki", "pages")


def test_server_project_configure_build_sync_gates(tmp_path, monkeypatch):
    _server_env(monkeypatch, tmp_path)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_record(workspace, _record())

    result, error = run("dy_wiki_build", {"confirmed": True}, workspace)
    assert error["code"] == -32602 and "禁止猜测" in error["message"]

    result, error = run("dy_project_configure", {**PROJECT, "confirmed": False}, workspace)
    assert error["code"] == -32602 and not (workspace / ".dy_superpowers/project.json").exists()
    result, error = run("dy_project_configure", {**PROJECT, "confirmed": True}, workspace)
    assert error is None and result["project_id"] and result["name_confirmed"] is True

    result, error = run("dy_wiki_build", {"confirmed": False}, workspace)
    assert error["code"] == -32602
    result, error = run("dy_wiki_build", {"confirmed": True}, workspace)
    assert error is None and result["built"] and result["pages"] == 3 and result["published"] is False

    for key in ("DY_WIKI_API_URL", "DY_WIKI_TOKEN", "DY_WIKI_PROJECT_ID",
                "DY_WIKI_GROUP_ID", "DY_WIKI_PROJECT_NAME"):
        monkeypatch.delenv(key, raising=False)
    result, error = run("dy_wiki_sync", {"confirmed": False}, workspace)
    assert error["code"] == -32602
    result, error = run("dy_wiki_sync", {"confirmed": True}, workspace)
    assert error["code"] == -32602 and "DY_WIKI_API_URL" in error["message"]
    monkeypatch.setenv("DY_WIKI_API_URL", "https://gitlab.example.com/api/v4")
    monkeypatch.setenv("DY_WIKI_TOKEN", "pat-x")
    monkeypatch.setenv("DY_WIKI_PROJECT_ID", "123")
    # 不发真实网络请求：会话建立即失败映射为受控错误，完整远端流程由 test_wiki_sync 覆盖。
    class _BrokenSession:
        def __init__(self):
            raise ConnectionError("no network in unit test")
    monkeypatch.setattr("requests.Session", _BrokenSession)
    result, error = run("dy_wiki_sync", {"confirmed": True}, workspace)
    assert error["code"] == -32602

    fresh = tmp_path / "fresh"
    fresh.mkdir()
    result, error = run("dy_wiki_sync", {"confirmed": True}, fresh)
    assert error["code"] == -32602 and "禁止猜测" in error["message"]  # 项目门禁先于清单门禁
