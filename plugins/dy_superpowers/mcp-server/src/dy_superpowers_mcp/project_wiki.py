"""项目身份、中文导航与可复核 Wiki 构建清单；生成不改变调研事实。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import unicodedata
from uuid import UUID, uuid4

from .memory import module_memory_files
from .research import read_module_records
from .storage import atomic_write, record_lock

CODE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PAGE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
MODULE_NAMES = {"expense": "费用管理", "general-ledger": "总账管理"}


def safe_path(workspace, *parts):
    workspace = Path(workspace).resolve()
    current = workspace
    for part in (".dy_superpowers", *parts):
        current = current / part
        if current.is_symlink() or not current.resolve().is_relative_to(workspace):
            raise ValueError("项目配置与 Wiki 路径必须位于工作区且不能使用符号链接")
    return current


def name_key(name):
    return " ".join(unicodedata.normalize("NFKC", name).split()).casefold()


def configure_project(workspace, name, code, *, confirmed=False):
    if confirmed is not True:
        raise PermissionError("项目名称与代号须由用户确认，不能用账套名推断")
    if not isinstance(name, str) or not name.strip() or len(name) > 100 or any(ord(c) < 32 for c in name):
        raise ValueError("项目名称须为1至100字的单行文本")
    if not isinstance(code, str) or not CODE.fullmatch(code) or len(code) > 32:
        raise ValueError("项目代号须为不超过32字的小写字母、数字和分隔连字符")
    path = safe_path(workspace, "project.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with record_lock(path):
        if path.exists():
            data = load_project(workspace)
            if data["name"] != name.strip() or data["code"] != code:
                raise ValueError("项目已绑定，不能改名或改代号隐式创建第二个项目；需显式迁移")
            return data
        data = {"schema": "dy-project/v1", "project_id": str(uuid4()),
                "name": name.strip(), "code": code, "name_confirmed": True}
        atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return data


def load_project(workspace):
    path = safe_path(workspace, "project.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        UUID(data["project_id"])
        valid = (data.get("schema") == "dy-project/v1" and data.get("name_confirmed") is True
                 and isinstance(data["name"], str) and bool(data["name"].strip())
                 and len(data["name"]) <= 100 and not any(ord(c) < 32 for c in data["name"])
                 and isinstance(data["code"], str) and CODE.fullmatch(data["code"])
                 and len(data["code"]) <= 32)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError("项目身份缺失或旧配置未确认：由助手提出名称候选，经用户确认后配置项目，禁止猜测") from exc
    if not valid:
        raise ValueError("项目配置未确认或格式无效，须先确认唯一项目名称及代号")
    return data


def md(value):
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    return re.sub(r"([\\`*_{}\[\]<>()#+.!|])", r"\\\1", text)


def checksum(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _research_digest(workspace):
    # 覆盖每个模块 research.md 的当前全文；构建后调研记录发生变化会使清单失效。
    parts = []
    for path in sorted(module_memory_files(workspace), key=lambda item: item.parent.name):
        parts.append(path.parent.name + "\0" + path.read_text(encoding="utf-8"))
    return checksum("\0".join(parts))


def owner_marker(project_id, page_id):
    return "<!-- dy-wiki-owner:" + json.dumps({"project_id": project_id, "page_id": page_id}, separators=(",", ":")) + " -->"


def build_bundle(workspace):
    project = load_project(workspace)
    prefix = project["code"] + "-" + project["project_id"].replace("-", "")[:8]
    overview = prefix + "-overview"
    pages, entries = {}, []
    modules = []

    def add(slug, title, lines):
        body = owner_marker(project["project_id"], slug) + "\n\n" + "\n".join(lines).rstrip() + "\n"
        pages[slug] = body
        entries.append({"slug": slug, "title": title, "sha256": checksum(body)})

    for path in module_memory_files(workspace):
        module = path.parent.name
        records, offset = [], 0
        while True:
            batch, more = read_module_records(workspace, module, offset=offset, limit=100)
            records.extend(batch)
            if not more:
                break
            offset += 100
        label = MODULE_NAMES.get(module, module)
        # 检查顺序是稳定业务键，插入或重排其他项不改变现有主题地址。
        groups = {}
        for record in records:
            groups.setdefault(record["check_order"], []).append(record)
        index_slug = f"{prefix}-{module}-index"
        # 目录表两列：第一列业务主题本身即跳转链接，不再单设“跳转”列。
        lines = [f"# {md(label)}调研目录", "", f"[返回项目首页]({overview})", "",
                 "| 业务主题 | 已记录子项 |", "| --- | --- |"]
        recorded, retired = len(records), sum(r["status"] == "retired" for r in records)
        for order, items in sorted(groups.items()):
            digest = hashlib.sha256(f"{module}:{order}".encode()).hexdigest()[:12]
            topic_slug = f"{prefix}-{module}-topic-{digest}"
            title = items[0]["config_item"]
            detail = [f"# {md(title)}", "", f"**项目：{md(project['name'])}** · {md(label)}", "",
                      f"[项目首页]({overview}) · [本模块目录]({index_slug})", ""]
            for item in sorted(items, key=lambda r: (r["research_id"], r["scope_id"])):
                legacy_import = any(t in item["confirmed_by"] for t in ("授权导入", "用户授权"))
                status = "已停用" if item["status"] == "retired" else "来源记录，业务签认待核实" if legacy_import else "已记录，业务适用性须复核"
                detail += [f"## 子项 {md(item['research_id'])} · {status}", "",
                           f"**调研内容**：{md(item['conclusion'])}", "",
                           f"- 适用范围：{md(item['scope'])}",
                           f"- 来源及依据：{md(item.get('evidence_ref')) or '尚未提供'}",
                           f"- 记录的确认信息：{md(item['confirmed_by'])} / {md(item['confirmed_at'])}",
                           "- 说明：来源导入、业务签认和巡检通过是不同状态，不能相互替代。", ""]
            add(topic_slug, f"{label}｜{title}", detail)
            lines.append(f"| [{md(title)}]({topic_slug}) | {len(items)} |")
        add(index_slug, f"{label}调研目录", lines)
        modules.append((label, index_slug, recorded, retired))

    source_lines = ["## 原始调研资料", "", "原始文件按项目保存在 Git 仓库；本页只列已登记的原始文件，巡检报告 Markdown/PDF 不入库。", ""]
    source_file = safe_path(workspace, "sources.json")
    sources = json.loads(source_file.read_text(encoding="utf-8")) if source_file.exists() else []
    if not isinstance(sources, list):
        raise ValueError("原始文件清单 sources.json 必须是列表")
    if sources:
        from urllib.parse import quote, urlsplit
        for source in sources:
            if source.get("project_id") != project["project_id"]:
                raise ValueError("原始资料清单项目归属不匹配")
            name = source.get("name")
            if (not isinstance(name, str) or not name.strip() or len(name) > 200
                    or any(ord(c) < 32 for c in name)):
                raise ValueError("原始资料名称须为1至200字的单行文本")
            url = source.get("url", "")
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("原始文件链接必须是无凭据的 HTTP(S) 绝对地址")
            # 文件名本身即查看链接，不再单设“查看”列。
            source_lines.append(f"- [{md(name)}]({quote(url, safe=':/%?=&')})")
    else:
        source_lines.append("尚未登记原始文件链接；不显示猜测路径或已删除报告的链接。")
    lines = [f"# {md(project['name'])}", "", f"**唯一项目代号：{md(project['code'])}**", "",
             "## 从这里开始", "", "1. 按模块进入调研目录，选择要核对的业务主题。",
             "2. 查看调研内容、适用范围及来源；‘已记录’不等于业务已签认或验收通过。",
             "3. 需要更正时提出具体主题及修订内容，经确认更新工作区记忆，再发布本项目页面。", "",
             "## 模块导航", "", "| 模块 | 已记录子项 | 已停用 |", "| --- | --- | --- |"]
    for label, slug, count, retired in modules:
        lines.append(f"| [{md(label)}]({slug}) | {count} | {retired} |")
    if not modules:
        lines.append("| 尚无调研记录 | 0 | 0 |")
    add(overview, project["name"], lines + [""] + source_lines)
    return {"schema": "dy-project-wiki/v1", "project": project, "overview_slug": overview,
            "pages": pages, "entries": sorted(entries, key=lambda item: item["slug"])}


def stage_wiki(workspace):
    bundle = build_bundle(workspace)
    root = safe_path(workspace, "wiki")
    pages_dir = safe_path(workspace, "wiki", "pages")
    pages_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = safe_path(workspace, "wiki", "manifest.json")
    with record_lock(manifest_path):
        for entry in bundle["entries"]:
            path = safe_path(workspace, "wiki", "pages", entry["slug"] + ".md")
            atomic_write(path, bundle["pages"][entry["slug"]])
        manifest = {key: value for key, value in bundle.items() if key != "pages"}
        manifest["research_sha256"] = _research_digest(workspace)
        atomic_write(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def load_staged(workspace):
    project = load_project(workspace)
    path = safe_path(workspace, "wiki", "manifest.json")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("尚无有效 Wiki 构建清单，请先构建本项目页面") from exc
    if manifest.get("schema") != "dy-project-wiki/v1" or manifest.get("project") != project:
        raise ValueError("Wiki 清单与项目身份不一致，拒绝同步")
    if manifest.get("research_sha256") != _research_digest(workspace):
        raise ValueError("调研记录在 Wiki 构建后发生变化，请重新构建本项目页面")
    pages = {}
    for entry in manifest.get("entries", []):
        slug = entry.get("slug", "")
        prefix = project["code"] + "-" + project["project_id"].replace("-", "")[:8] + "-"
        if not PAGE.fullmatch(slug) or not slug.startswith(prefix) or slug in pages:
            raise ValueError("Wiki 清单包含越界或重复页面")
        content = safe_path(workspace, "wiki", "pages", slug + ".md").read_text(encoding="utf-8")
        if checksum(content) != entry.get("sha256") or not content.startswith(owner_marker(project["project_id"], slug)):
            raise ValueError("Wiki 构建页面已变更，请重新构建")
        pages[slug] = content
    if not pages or manifest.get("overview_slug") not in pages:
        raise ValueError("Wiki 清单缺少项目首页")
    return manifest, pages
