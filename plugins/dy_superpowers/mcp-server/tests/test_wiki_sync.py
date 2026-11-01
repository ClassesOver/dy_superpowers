"""GitLab Wiki 发布回归：mock API（Session.request seam），不发真实请求。

覆盖：确认门禁与环境校验、私有地址拒绝、清单发布、共享首页注册表、
命名唯一性、远端改动冲突、重定向拒绝、无删除、目标与状态绑定。
"""
import ipaddress
import json
import re
import socket
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import server
from dy_superpowers_mcp.project_wiki import configure_project, load_staged, stage_wiki
from dy_superpowers_mcp.research import write_record
from dy_superpowers_mcp.wiki_sync import WikiSyncError, sync_wiki

API = "https://gitlab.example.com/api/v4"


def _record(**overrides):
    base = {"module": "expense", "check_order": "1", "research_id": "01",
            "config_item": "应用安装", "conclusion": "费用基础应用需要安装并启用。",
            "scope": "示例医院/2026 上线", "confirmed_by": "业务负责人",
            "confirmed_at": "2026-10-20", "confirmed": True}
    base.update(overrides)
    return base


def _workspace(parent, name, code):
    workspace = parent / f"ws-{code}"
    workspace.mkdir(parents=True)
    configure_project(workspace, name, code, confirmed=True)
    write_record(workspace, _record(scope=f"{name}/2026"))
    stage_wiki(workspace)
    return workspace


class FakeGitLab:
    """只实现发布器用到的子集；slug 由 title 派生（真实 GitLab 行为）。"""

    def __init__(self):
        self.pages = {}       # slug -> content
        self.projects = {}    # pid -> {"id", "name", "namespace"}
        self.methods = []
        self.redirect = False
        self.private_hosts = {"gitlab.internal.local"}
        self.home_race_once = False  # 首个页面写入前模拟首页被并发更新一次

    class _Response:
        def __init__(self, status, payload):
            self.status_code = status
            self._payload = payload

        def json(self):
            return self._payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(str(self.status_code))

    def install(self, monkeypatch):
        gl = self

        class Session:
            def __init__(self):
                self.headers = {}
                self.trust_env = True

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def request(self, method, url, allow_redirects=False, timeout=None,
                        params=None, data=None):
                gl.methods.append((method, url))
                if gl.redirect:
                    return gl._Response(302, {})
                return gl.handle(method, url, params, data)

        monkeypatch.setattr("requests.Session", Session)

        def resolve(host, port, type=None):
            # DNS seam：测试不发真实 DNS；私网字面量/黑名单主机按真实语义解析。
            try:
                literal = ipaddress.ip_address(host)
                addr = host if not literal.is_global else "93.184.216.34"
            except ValueError:
                addr = "10.0.0.5" if host in gl.private_hosts else "93.184.216.34"
            return [(2, 1, 6, "", (addr, port))]

        monkeypatch.setattr(socket, "getaddrinfo", resolve)
        return gl

    def handle(self, method, url, params, data):
        path = urlsplit(url).path
        if method == "GET" and re.search(r"/groups/\d+/projects$", path):
            name = (params or {}).get("search", "")
            return self._Response(200, [p for p in self.projects.values() if p["name"] == name])
        if method == "POST" and re.search(r"/groups/\d+/projects$", path) is None \
                and path.endswith("/projects"):
            pid = 900 + len(self.projects)
            self.projects[str(pid)] = {"id": pid, "name": data["name"],
                                       "namespace": {"id": int(data["namespace_id"])}}
            return self._Response(201, self.projects[str(pid)])
        match = re.search(r"/projects/(\d+)/wikis(?:/([^/]+))?$", path)
        if match:
            slug = unquote(match.group(2)) if match.group(2) else None
            if method == "GET" and slug:
                if slug in self.pages:
                    return self._Response(200, {"slug": slug, "content": self.pages[slug]})
                return self._Response(404, {})
            if method in {"POST", "PUT"}:
                if self.home_race_once and slug != "home" and "home" in self.pages:
                    self.home_race_once = False
                    self.pages["home"] += "并发写入"  # 首页在同步窗口内被他人更新
                key = slug if method == "PUT" else data["title"]
                self.pages[key] = data["content"]
                return self._Response(200 if method == "PUT" else 201,
                                      {"slug": key, "content": data["content"]})
        raise AssertionError("unexpected request: " + method + " " + url)

    def assert_no_delete_and_no_token(self):
        assert all(method != "DELETE" for method, _ in self.methods)
        assert all("pat-test" not in url for _, url in self.methods)


def _env(monkeypatch, api=API, pid="123", group=None, name=None):
    monkeypatch.setenv("DY_WIKI_API_URL", api)
    monkeypatch.setenv("DY_WIKI_TOKEN", "pat-test")
    if pid is None:
        monkeypatch.delenv("DY_WIKI_PROJECT_ID", raising=False)
    else:
        monkeypatch.setenv("DY_WIKI_PROJECT_ID", pid)
    if group is None:
        monkeypatch.delenv("DY_WIKI_GROUP_ID", raising=False)
    else:
        monkeypatch.setenv("DY_WIKI_GROUP_ID", group)
    if name is None:
        monkeypatch.delenv("DY_WIKI_PROJECT_NAME", raising=False)
    else:
        monkeypatch.setenv("DY_WIKI_PROJECT_NAME", name)


def _server_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))


def test_sync_confirmation_project_and_manifest_gates(tmp_path, monkeypatch):
    _server_env(monkeypatch, tmp_path)
    gl = FakeGitLab().install(monkeypatch)
    _env(monkeypatch)
    empty = tmp_path / "empty"
    empty.mkdir()
    result, error = server.execute_tool("dy_wiki_sync", {"trace_id": "t", "confirmed": True}, empty)
    assert result is None and error is not None

    workspace = tmp_path / "ws"
    workspace.mkdir()
    configure_project(workspace, "示例医院费用项目", "demo-a", confirmed=True)
    result, error = server.execute_tool("dy_wiki_sync", {"trace_id": "t", "confirmed": True}, workspace)
    assert result is None and error is not None

    write_record(workspace, _record())
    stage_wiki(workspace)
    result, error = server.execute_tool("dy_wiki_sync", {"trace_id": "t", "confirmed": True}, workspace)
    assert error is None and result["synced"]

    for key in ("DY_WIKI_API_URL", "DY_WIKI_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    result, error = server.execute_tool("dy_wiki_sync", {"trace_id": "t", "confirmed": True}, workspace)
    assert result is None and "DY_WIKI_API_URL" in error["message"]


def test_private_or_credentialed_target_rejected_before_request(tmp_path, monkeypatch):
    gl = FakeGitLab().install(monkeypatch)
    workspace = _workspace(tmp_path, "示例项目", "demo-a")
    for api in ("https://127.0.0.1/api/v4", "https://gitlab.internal.local/api/v4",
                "https://user:pat-test@gitlab.example.com/api/v4"):
        _env(monkeypatch, api=api)
        with pytest.raises(WikiSyncError, match="公网|凭据"):
            sync_wiki(workspace, confirmed=True)
    assert gl.methods == []  # 校验发生在任何网络请求之前


def test_publish_manifest_then_second_run_unchanged(tmp_path, monkeypatch):
    gl = FakeGitLab().install(monkeypatch)
    _env(monkeypatch)
    workspace = _workspace(tmp_path, "示例医院费用项目", "demo-a")
    manifest, pages = load_staged(workspace)

    result = sync_wiki(workspace, confirmed=True)
    assert result["synced"] and result["pages"] == len(pages)
    assert result["created"] == len(pages) and result["updated"] == result["unchanged"] == 0
    assert result["deleted"] == 0 and result["home_maintained"]
    assert gl.pages["home"].startswith("# 项目调研知识库")
    assert "示例医院费用项目" in gl.pages["home"] and "阅读指引" in gl.pages["home"]
    assert "<!-- dy-wiki-registry:" in gl.pages["home"]
    # 两列目录：项目名称本身即入口链接，不单设“入口”列。
    assert "| 入口 |" not in gl.pages["home"] and "[示例医院费用项目](" in gl.pages["home"]
    for slug, content in pages.items():
        assert gl.pages[slug] == content
    gl.assert_no_delete_and_no_token()

    again = sync_wiki(workspace, confirmed=True)
    assert again["unchanged"] == len(pages) and again["created"] == again["updated"] == 0
    assert gl.pages["home"].count("示例医院费用项目") >= 1  # 幂等，不重复登记
    registry = json.loads(gl.pages["home"].split("dy-wiki-registry:", 1)[1].rsplit("-->", 1)[0])
    assert len(registry) == 1
    gl.assert_no_delete_and_no_token()


def test_second_project_shares_home_without_touching_first(tmp_path, monkeypatch):
    gl = FakeGitLab().install(monkeypatch)
    _env(monkeypatch)
    first = _workspace(tmp_path, "示例医院费用项目", "demo-a")
    second = _workspace(tmp_path, "北院区财务一体化", "north-fin")
    sync_wiki(first, confirmed=True)
    first_pages = dict(gl.pages)

    result = sync_wiki(second, confirmed=True)
    assert result["synced"] and result["project_name"] == "北院区财务一体化"
    assert "示例医院费用项目" in gl.pages["home"] and "北院区财务一体化" in gl.pages["home"]
    registry = json.loads(gl.pages["home"].split("dy-wiki-registry:", 1)[1].rsplit("-->", 1)[0])
    assert len(registry) == 2
    # 首页只增加第二个项目行；第一个项目的页面原样保留。
    for slug, content in first_pages.items():
        if slug != "home":
            assert gl.pages[slug] == content
    gl.assert_no_delete_and_no_token()


def test_duplicate_name_or_code_refused_before_write(tmp_path, monkeypatch):
    gl = FakeGitLab().install(monkeypatch)
    _env(monkeypatch)
    first = _workspace(tmp_path / "a", "示例医院费用项目", "demo-a")
    sync_wiki(first, confirmed=True)
    home_before, pages_before = gl.pages["home"], dict(gl.pages)

    # 尾随全角空格：NFKC 归一后与现有项目同名，写页前必须拒绝。
    same_name = _workspace(tmp_path / "b", "示例医院费用项目　", "other-code")
    with pytest.raises(WikiSyncError, match="重复"):
        sync_wiki(same_name, confirmed=True)
    same_code = _workspace(tmp_path / "c", "另一个项目名", "demo-a")
    with pytest.raises(WikiSyncError, match="重复"):
        sync_wiki(same_code, confirmed=True)
    assert gl.pages["home"] == home_before and gl.pages == pages_before


def test_unmanaged_home_and_registry_damage_refused(tmp_path, monkeypatch):
    gl = FakeGitLab().install(monkeypatch)
    _env(monkeypatch)
    workspace = _workspace(tmp_path, "示例医院费用项目", "demo-a")
    gl.pages["home"] = "# 其他团队维护的首页\n"
    with pytest.raises(WikiSyncError, match="拒绝覆盖"):
        sync_wiki(workspace, confirmed=True)
    assert gl.pages["home"] == "# 其他团队维护的首页\n"

    gl.pages["home"] = "# 项目调研知识库\n<!-- dy-wiki-registry:broken -->\n"
    with pytest.raises(WikiSyncError, match="登记表损坏"):
        sync_wiki(workspace, confirmed=True)


def test_remote_edits_conflict_refused(tmp_path, monkeypatch):
    gl = FakeGitLab().install(monkeypatch)
    _env(monkeypatch)
    workspace = _workspace(tmp_path, "示例医院费用项目", "demo-a")
    manifest, pages = load_staged(workspace)
    sync_wiki(workspace, confirmed=True)

    topic = next(slug for slug in pages if "-topic-" in slug)
    gl.pages[topic] += "\n业务同事手工补充"  # 保留所有权标记，但偏离上次同步哈希
    with pytest.raises(WikiSyncError, match="拒绝覆盖"):
        sync_wiki(workspace, confirmed=True)

    gl.pages[topic] = "<!-- dy-wiki-owner:{\"project_id\":\"别的项目\",\"page_id\":\"x\"} -->\n被他人接管"
    with pytest.raises(WikiSyncError, match="拒绝覆盖"):
        sync_wiki(workspace, confirmed=True)


def test_redirect_rejected_without_follow(tmp_path, monkeypatch):
    gl = FakeGitLab().install(monkeypatch)
    gl.redirect = True
    _env(monkeypatch)
    workspace = _workspace(tmp_path, "示例医院费用项目", "demo-a")
    with pytest.raises(WikiSyncError, match="重定向"):
        sync_wiki(workspace, confirmed=True)
    assert gl.pages == {}  # 未跟随重定向，未写入任何页面


def test_home_concurrent_update_detected(tmp_path, monkeypatch):
    gl = FakeGitLab().install(monkeypatch)
    _env(monkeypatch)
    workspace = _workspace(tmp_path, "示例医院费用项目", "demo-a")
    sync_wiki(workspace, confirmed=True)
    gl.home_race_once = True
    # 本地内容变化触发页面写入；写入期间首页被他人更新 → 拒绝合并并保留已发布页面。
    write_record(workspace, _record(research_id="02", conclusion="补充第二条调研结论。"))
    stage_wiki(workspace)
    with pytest.raises(WikiSyncError, match="首页被更新"):
        sync_wiki(workspace, confirmed=True)
    assert "并发写入" in gl.pages["home"]


def test_lazy_project_creation_and_instance_binding(tmp_path, monkeypatch):
    gl = FakeGitLab().install(monkeypatch)
    _env(monkeypatch, pid=None, group="42", name="研究知识库")
    workspace = _workspace(tmp_path, "示例医院费用项目", "demo-a")
    result = sync_wiki(workspace, confirmed=True)
    assert result["project_id"] == "900"
    target = json.loads((workspace / ".dy_superpowers/wiki-target.json").read_text(encoding="utf-8"))
    assert target == {"api": API, "project_id": "900"}

    _env(monkeypatch, pid=None)  # 复用持久化目标
    again = sync_wiki(workspace, confirmed=True)
    assert again["unchanged"] == again["pages"]

    _env(monkeypatch, pid=None, api="https://gitlab2.example.com/api/v4")
    with pytest.raises(WikiSyncError, match="实例不同"):
        sync_wiki(workspace, confirmed=True)


def test_state_identity_change_refused(tmp_path, monkeypatch):
    gl = FakeGitLab().install(monkeypatch)
    _env(monkeypatch, pid="123")
    workspace = _workspace(tmp_path, "示例医院费用项目", "demo-a")
    sync_wiki(workspace, confirmed=True)
    _env(monkeypatch, pid="456")  # 同一工作区换目标项目
    with pytest.raises(WikiSyncError, match="其他实例或项目"):
        sync_wiki(workspace, confirmed=True)
