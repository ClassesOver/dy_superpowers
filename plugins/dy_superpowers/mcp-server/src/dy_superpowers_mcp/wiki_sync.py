"""发布已构建的本项目 Wiki 清单；不扫描旧 notes、不删除远端页、不覆盖他人页面。"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
from urllib.parse import quote, urlsplit
from pathlib import Path

from .project_wiki import checksum, load_staged, md, name_key, owner_marker, safe_path
from .storage import atomic_write, record_lock

REGISTRY = re.compile(r"<!-- dy-wiki-registry:(.*?) -->", re.DOTALL)


class WikiSyncError(ValueError):
    """错误信息不得包含凭据或远端响应正文。"""


def _validate_public_url(url):
    try:
        parsed = urlsplit(url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment or "\\" in url
                or any(c.isspace() for c in url)):
            raise ValueError
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(info[4][0]).is_global for info in addresses):
            raise ValueError
    except (ValueError, OSError) as exc:
        raise WikiSyncError("GitLab 地址须为解析到公网地址的 HTTP(S)，禁止凭据、重定向及私有/保留地址") from exc


def _settings():
    api = os.environ.get("DY_WIKI_API_URL", "").strip().rstrip("/")
    token = os.environ.get("DY_WIKI_TOKEN", "").strip()
    if not api or not token:
        raise WikiSyncError("缺少 DY_WIKI_API_URL / DY_WIKI_TOKEN")
    _validate_public_url(api)
    return {"api": api, "token": token,
            "project_id": os.environ.get("DY_WIKI_PROJECT_ID", "").strip(),
            "group_id": os.environ.get("DY_WIKI_GROUP_ID", "").strip(),
            "project_name": os.environ.get("DY_WIKI_PROJECT_NAME", "").strip()}


def _request(session, method, url, **kwargs):
    # 每个请求校验实际目标；同时禁代理与重定向。部署防火墙仍是 DNS 重绑定的网络边界。
    _validate_public_url(url)
    response = session.request(method, url, allow_redirects=False, timeout=20, **kwargs)
    if 300 <= response.status_code < 400:
        raise WikiSyncError("GitLab 返回重定向，拒绝把令牌转发到其他地址")
    return response


def _json(response):
    response.raise_for_status()
    try:
        return response.json()
    except ValueError as exc:
        raise WikiSyncError("GitLab 返回无效 JSON，未回显正文") from exc


def _resolve_project(session, settings, workspace):
    explicit = settings["project_id"]
    if explicit:
        if not explicit.isdigit():
            raise WikiSyncError("DY_WIKI_PROJECT_ID 请使用数字项目 ID")
        return explicit
    target = safe_path(workspace, "wiki-target.json")
    if target.exists():
        try:
            saved = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise WikiSyncError("持久化 Wiki 目标文件损坏，须人工核对后重新配置目标") from exc
        if saved.get("api") != settings["api"] or not str(saved.get("project_id", "")).isdigit():
            raise WikiSyncError("持久化 Wiki 目标缺少实例绑定或与当前实例不同，须显式重新配置目标")
        return str(saved["project_id"])
    group, name = settings["group_id"], settings["project_name"]
    if not group.isdigit() or not name or len(name) > 100 or any(ord(c) < 32 for c in name):
        raise WikiSyncError("设置数字 DY_WIKI_PROJECT_ID，或 DY_WIKI_GROUP_ID 和 DY_WIKI_PROJECT_NAME")
    for page in range(1, 101):
        rows = _json(_request(session, "GET", f"{settings['api']}/groups/{group}/projects",
                             params={"search": name, "page": page, "per_page": 100}))
        if not isinstance(rows, list):
            raise WikiSyncError("GitLab 组项目列表结构无效")
        matches = [row for row in rows if row.get("name") == name
                   and str(row.get("namespace", {}).get("id")) == group]
        if len(matches) > 1:
            raise WikiSyncError("目标组有重名项目，须使用数字 ID 明确目标")
        if matches:
            project = str(matches[0]["id"])
            break
        if len(rows) < 100:
            created = _json(_request(session, "POST", f"{settings['api']}/projects",
                                    data={"name": name, "namespace_id": group,
                                          "wiki_enabled": "true", "visibility": "private"}))
            project = str(created["id"])
            break
    else:
        raise WikiSyncError("组项目列表未完整读取，拒绝猜测目标或创建重复项目")
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(target, json.dumps({"api": settings["api"], "project_id": project}, indent=2) + "\n")
    return project


def _detail(session, base, slug):
    response = _request(session, "GET", f"{base}/{quote(slug, safe='')}")
    if response.status_code == 404:
        return None
    result = _json(response)
    if not isinstance(result, dict) or not isinstance(result.get("content"), str):
        raise WikiSyncError("GitLab Wiki 页面结构无效")
    return result


def _registry(home):
    if home is None:
        return {}
    marker = REGISTRY.search(home["content"])
    if not marker:
        raise WikiSyncError("远端首页不是本工具管理的门户，拒绝覆盖；请先明确迁移旧首页")
    try:
        value = json.loads(marker.group(1))
        if not isinstance(value, dict) or any(not isinstance(v, dict) for v in value.values()):
            raise ValueError
        return value
    except ValueError as exc:
        raise WikiSyncError("远端项目登记表损坏，拒绝覆盖首页") from exc


def _check_registration(registry, project):
    for pid, entry in registry.items():
        if pid == project["project_id"]:
            if entry.get("name") != project["name"] or entry.get("code") != project["code"]:
                raise WikiSyncError("相同项目 ID 的名称或代号变更，须显式迁移")
            continue
        if name_key(entry.get("name", "")) == name_key(project["name"]) or entry.get("code") == project["code"]:
            raise WikiSyncError("项目名称或代号与知识库现有项目重复，请确认唯一名称及代号")


def _home_content(registry):
    # 两列项目目录：项目名称本身即入口链接，不单设“入口”列。
    lines = ["# 项目调研知识库", "", "## 选择项目", "",
             "| 项目名称 | 项目代号 |", "| --- | --- |"]
    for entry in sorted(registry.values(), key=lambda e: name_key(e["name"])):
        lines.append(f"| [{md(entry['name'])}]({entry['overview_slug']}) | {md(entry['code'])} |")
    lines += ["", "## 阅读指引", "", "1. 选择项目，再进入费用或总账等模块的业务主题。",
              "2. 阅读调研内容、适用范围和来源；已记录不代表业务签认或巡检通过。",
              "3. 从项目首页查看原始调研文件。巡检报告不保存在此 Git 仓库。", ""]
    # 元数据藏在 HTML 注释中，GitLab 展示不出现 frontmatter。
    return "\n".join(lines) + "<!-- dy-wiki-registry:" + json.dumps(registry, ensure_ascii=True, separators=(",", ":")) + " -->\n"


def sync_wiki(workspace, *, confirmed=False):
    if confirmed is not True:
        raise PermissionError("Wiki 同步是外发动作，需要用户明确确认")
    import requests

    workspace = Path(workspace).resolve()
    manifest, pages = load_staged(workspace)
    settings = _settings()
    project = manifest["project"]
    state_path = safe_path(workspace, "wiki", "sync-state.json")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with record_lock(state_path), requests.Session() as session:
        session.trust_env = False
        session.headers["PRIVATE-TOKEN"] = settings["token"]
        try:
            target = _resolve_project(session, settings, workspace)
            base = f"{settings['api']}/projects/{target}/wikis"
            old_home = _detail(session, base, "home")
            registry = _registry(old_home)
            _check_registration(registry, project)
            try:
                state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
                if not isinstance(state, dict):
                    raise ValueError
            except (OSError, ValueError) as exc:
                raise WikiSyncError("本地同步状态损坏，须人工核对后处理；直接删除会拒绝覆盖远端已改页面") from exc
            identity = {"api": settings["api"], "target": target, "project_id": project["project_id"]}
            if state and state.get("identity") != identity:
                raise WikiSyncError("同步状态绑定其他实例或项目，拒绝隐式更换发布目标")
            hashes = state.get("hashes", {})
            remote, conflicts = {}, []
            for slug, content in pages.items():
                current = _detail(session, base, slug)
                remote[slug] = current
                if current and current["content"] != content:
                    if not current["content"].startswith(owner_marker(project["project_id"], slug)):
                        conflicts.append(slug)
                    elif hashes.get(slug) != checksum(current["content"]):
                        conflicts.append(slug)
            if conflicts:
                raise WikiSyncError(f"{len(conflicts)} 个页面无有效所有权或被远端修改，拒绝覆盖")
            created = updated = unchanged = 0
            for slug, content in pages.items():
                current = remote[slug]
                if current and current["content"] == content:
                    unchanged += 1
                else:
                    method = "PUT" if current else "POST"
                    url = f"{base}/{quote(slug, safe='')}" if current else base
                    # GitLab 通过 title 派生 slug：传稳定页面键，中文标题放在页面 H1/导航文字中。
                    response = _request(session, method, url, data={"title": slug, "content": content})
                    result = _json(response)
                    if not isinstance(result, dict) or result.get("slug") != slug or result.get("content") != content:
                        raise WikiSyncError("GitLab 页面键或回读内容不一致，停止同步")
                    if current:
                        updated += 1
                    else:
                        created += 1
                hashes[slug] = checksum(content)
                atomic_write(state_path, json.dumps({"identity": identity, "hashes": hashes}, indent=2) + "\n")
            current_home = _detail(session, base, "home")
            if (current_home or {}).get("content") != (old_home or {}).get("content"):
                raise WikiSyncError("同步期间首页被更新，项目页面已保存，请重试合并导航")
            registry[project["project_id"]] = {"name": project["name"], "code": project["code"],
                                                  "overview_slug": manifest["overview_slug"]}
            home = _home_content(registry)
            if not old_home or old_home["content"] != home:
                url = f"{base}/home" if old_home else base
                result = _json(_request(session, "PUT" if old_home else "POST", url,
                                        data={"title": "home", "content": home}))
                if result.get("content") != home:
                    raise WikiSyncError("首页回读校验失败")
        except requests.RequestException as exc:
            raise WikiSyncError("GitLab 请求失败；凭据与响应正文不回显，本地页面保留，可重试") from exc
    return {"synced": True, "project_id": target, "local_project_id": project["project_id"],
            "project_name": project["name"], "pages": len(pages), "created": created,
            "updated": updated, "unchanged": unchanged, "deleted": 0,
            "home_maintained": True, "note": "只同步本项目构建清单；不删除页面，不推交付物或原始文件"}
