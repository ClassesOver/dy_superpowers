"""调研→项目身份→本地 Wiki 构建→GitLab 发布端到端（真实 stdio MCP + mock GitLab API）。

链路：xlsx 解析（脚本函数直调）→ dy_research_record_write（确认门禁）
→ dy_notes_write（自由主题记忆，与发布清单分离）→ dy_project_configure（项目身份确认）
→ dy_wiki_build（本地清单）→ dy_wiki_sync（mock GitLab，含共享首页注册表）。
真实 Excel 不入库：临时生成最小 xlsx 夹具。
"""
import asyncio
import importlib.util
import ipaddress
import json
import os
import re
import socket
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

pytest.importorskip("openpyxl")
from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

PLUGIN = Path(__file__).parents[2]
API = "https://gitlab.example.com/api/v4"
PROJECT_NAME = "E2E示例医院项目"
PROJECT_CODE = "e2e-hosp"


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, PLUGIN / "skills/module-research/scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture_xlsx(path: Path) -> None:
    book = Workbook()
    sheet = book.active
    sheet.title = "调研清单"
    sheet.append(["检查编号", "配置项", "配置路径", "调研问题", "适用条件", "建议材料", "调研结果/本点结论"])
    sheet.append(["DY-01", "应用安装", "设置>应用管理", "问：范围？", "费用必查", "交付范围表",
                  "01｜费用模块基础应用：需要安装并启用。"])
    sheet.append(["DY-01", "应用安装", "设置>应用管理", "问：OCR？", "按需", "联调样例",
                  "02｜OCR相关模块：需要安装；需确认实际服务。"])
    book.save(path)


def _gitlab_mock(monkeypatch, state):
    def resp(payload, code):
        class R:
            status_code = code

            def json(self):
                return payload

            def raise_for_status(self):
                if self.status_code >= 400:
                    import requests
                    raise requests.HTTPError(str(code))
        return R()

    class Session:
        def __init__(self):
            self.headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def request(self, method, url, allow_redirects=False, timeout=None, params=None, data=None):
            state["methods"].append((method, url))
            path = urlsplit(url).path
            match = re.search(r"/projects/(\d+)/wikis(?:/([^/]+))?$", path)
            assert match, f"unexpected {method} {url}"
            slug = unquote(match.group(2)) if match.group(2) else None
            if method == "GET" and slug:
                if slug in state["pages"]:
                    return resp({"slug": slug, "content": state["pages"][slug]}, 200)
                return resp({}, 404)
            key = slug if method == "PUT" else data["title"]
            state["pages"][key] = data["content"]
            return resp({"slug": key, "content": data["content"]}, 200 if method == "PUT" else 201)

    monkeypatch.setattr("requests.Session", Session)
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda host, port, type=None: [(2, 1, 6, "", ("93.184.216.34", port))])


@pytest.mark.asyncio
async def test_research_to_project_wiki_pipeline(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    env = {"CODEX_HOME": str(tmp_path / "codex"), "DY_WORKSPACE_ROOT": str(workspace)}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    config = json.loads((PLUGIN / ".mcp.json").read_text())["mcpServers"]["dy-superpowers-odoo"]

    # 1) 调研来源解析（脚本函数直调，不经子进程）
    research_sources = _load_script("research_sources")
    source = tmp_path / "research-input.xlsx"
    _fixture_xlsx(source)
    manifest = research_sources.parse_source(source, "expense")
    assert manifest["coverage"]["source_subitems"] == 2
    assert manifest["records"][0]["research_id"] == "01"
    assert manifest["records"][1]["contains_pending_questions"] is True

    state = {"pages": {}, "methods": []}

    async with stdio_client(StdioServerParameters(
            command=config["command"],
            args=[a.replace("${PLUGIN_ROOT}", str(PLUGIN)) for a in config["args"]],
            cwd=str(workspace), env={**os.environ, **env})) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()

            async def call(tool, arguments, expect_error=False):
                from mcp.shared.exceptions import MCPError

                try:
                    response = await session.call_tool(tool, {"trace_id": "e2e-wiki", **arguments})
                except MCPError:
                    assert expect_error, "调用不应失败"
                    return None
                assert not response.model_dump(by_alias=True).get("isError", False) or expect_error
                if expect_error:
                    return None
                return response.content[0].text

            # 2) 检查键调研写入：未确认拒绝 → 确认后写入并回读
            research = {"module": "expense", "check_order": "1", "research_id": "01",
                        "config_item": "应用安装", "scope": "E2E 公司/2026",
                        "conclusion": "费用基础应用需要安装并启用。",
                        "confirmed_by": "E2E 顾问", "confirmed_at": "2026-10-24"}
            assert await call("dy_research_record_write", {**research, "confirmed": False}, expect_error=True) is None
            saved = json.loads(await call("dy_research_record_write", {**research, "confirmed": True}))
            record = json.loads(await call("dy_module_memory_read",
                                           {"module": "expense", "check_order": "1", "research_id": "01",
                                            "scope_id": saved["scope_id"]}))["records"][0]
            assert record["status"] == "confirmed" and record["revision"] == saved["revision"]

            # 3) 自由主题记忆走 notes，与发布清单分离
            note = {"slug": "expense-policy-note", "title": "费用政策口径",
                    "body": "住宿费禁止超标，见 [[project-research-index]]。", "tags": ["expense"]}
            await call("dy_notes_write", {**note, "confirmed": False}, expect_error=True)
            await call("dy_notes_write", {**note, "confirmed": True})
            listing = json.loads(await call("dy_notes_list", {}))
            assert listing["notes"] == ["expense-policy-note"]

            # 4) 项目身份：未配置时构建拒绝 → 确认后绑定唯一身份
            await call("dy_wiki_build", {"confirmed": True}, expect_error=True)
            await call("dy_project_configure", {"name": PROJECT_NAME, "code": PROJECT_CODE,
                                                "confirmed": False}, expect_error=True)
            project = json.loads(await call("dy_project_configure",
                                            {"name": PROJECT_NAME, "code": PROJECT_CODE, "confirmed": True}))
            assert project["name"] == PROJECT_NAME and project["code"] == PROJECT_CODE

            # 5) 本地构建：未确认拒绝 → 确认后生成项目首页/模块目录/主题页清单
            await call("dy_wiki_build", {"confirmed": False}, expect_error=True)
            built = json.loads(await call("dy_wiki_build", {"confirmed": True}))
            assert built["built"] and built["pages"] == 3 and built["published"] is False
            overview_slug = built["overview_slug"]
            assert overview_slug == f"{PROJECT_CODE}-{project['project_id'].replace('-', '')[:8]}-overview"
            assert "expense-policy-note" not in overview_slug  # notes 不进入发布清单

            # 6) GitLab 发布：确认门禁 → 清单页 + 共享首页注册表 → 二次同步幂等。
            #    同进程调用以便注入 mock GitLab（跨进程 monkeypatch 无效）；协议层已由
            #    test_wiki_sync 的 stdio 用例覆盖。
            import server as server_module

            monkeypatch.setattr(server_module, "initialize_telemetry", lambda: False)
            _gitlab_mock(monkeypatch, state)
            monkeypatch.setenv("DY_WIKI_API_URL", API)
            monkeypatch.setenv("DY_WIKI_TOKEN", "pat-e2e")
            monkeypatch.setenv("DY_WIKI_PROJECT_ID", "123")
            _, error = server_module.execute_tool("dy_wiki_sync", {"trace_id": "e2e-wiki", "confirmed": False}, workspace)
            assert error["code"] == -32602 and state["methods"] == []
            result, error = server_module.execute_tool("dy_wiki_sync", {"trace_id": "e2e-wiki", "confirmed": True}, workspace)
            assert error is None and result["synced"] and result["pages"] == 3
            assert result["created"] == 3 and result["deleted"] == 0 and result["home_maintained"]
            assert set(state["pages"]) == {"home", overview_slug,
                                           f"{PROJECT_CODE}-{project['project_id'].replace('-', '')[:8]}-expense-index",
                                           next(slug for slug in state["pages"] if "-topic-" in slug)}
            assert PROJECT_NAME in state["pages"]["home"] and "阅读指引" in state["pages"]["home"]
            assert all("pat-e2e" not in url for _, url in state["methods"])  # 令牌只进头部
            assert all(method != "DELETE" for method, _ in state["methods"])

            again, error = server_module.execute_tool("dy_wiki_sync", {"trace_id": "e2e-wiki", "confirmed": True}, workspace)
            assert error is None and again["unchanged"] == 3 and again["created"] == again["updated"] == 0
