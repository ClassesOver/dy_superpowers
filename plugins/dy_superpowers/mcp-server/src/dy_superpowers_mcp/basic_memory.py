"""插件必需的 Basic Memory 搜索后端，复用现有 MCP 服务。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .memory import MEMORY_FILES, WorkspaceMemory, module_memory_files, validate_module


BASIC_MEMORY_VERSION = "0.23.2"
BASIC_MEMORY_PROJECT = "dy-superpowers"


class BasicMemoryAdapter:
    """Search workspace memory through Basic Memory without registering another MCP server.

    The Markdown files under ``.dy_superpowers`` remain authoritative. Basic Memory's
    SQLite/config state is placed in a hidden workspace sibling directory and is
    constrained to one project, so a process started in another workspace cannot reuse it.
    """

    def __init__(self, workspace: str | Path, command: str | None = None) -> None:
        self.workspace = Path(workspace).resolve()
        self.memory_root = self.workspace / ".dy_superpowers"
        # Keep Basic Memory's database/config outside the indexed Markdown tree.
        # The Markdown files remain the only workspace memory source of truth.
        self.config_dir = self.workspace / ".dy_superpowers-basic-memory"
        self.command = command or os.environ.get("DY_BASIC_MEMORY_COMMAND") or str(Path(sys.executable).with_name("basic-memory"))

    def search(self, query: str, *, module: str | None = None) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("搜索关键词必须是非空文本")
        query = query.strip()
        if query.startswith("-"):
            # 查询词会原样作为 argv 传给 basic-memory CLI，前导连字符会被当作选项解析。
            raise ValueError("搜索关键词不能以连字符开头")
        if module is not None:
            module = validate_module(module)
        self._validate_paths()
        try:
            self._ensure_project()
            self._run(["reindex", "--search", "--project", BASIC_MEMORY_PROJECT])
            results: list[dict[str, Any]] = []
            upstream_has_more = False
            page = 1
            while page <= 100:
                output = self._run(
                    ["tool", "search-notes", query, "--json", "--local", "--project",
                     BASIC_MEMORY_PROJECT, "--page-size", "20", "--page", str(page)]
                )
                payload = json.loads(output)
                rows = payload.get("results") if isinstance(payload, dict) else payload
                if not isinstance(rows, list):
                    raise ValueError("Basic Memory 返回了无效搜索结果")
                results.extend(self._filter_results(rows, module))
                upstream_has_more = (bool(payload.get("has_more", False)) or len(rows) >= 20
                                     if isinstance(payload, dict) else len(rows) >= 20)
                if module is None or not upstream_has_more or len(results) >= 20:
                    break
                page += 1
            has_more = upstream_has_more or page > 100
            return {
                "backend": "basic-memory",
                "available": True,
                "expected_version": BASIC_MEMORY_VERSION,
                "project": BASIC_MEMORY_PROJECT,
                "results": results[:20],
                "has_more": has_more or len(results) > 20,
                "completeness_note": "搜索仅用于定位，满页时可能还有结果；使用 dy_memory_read 或 dy_module_memory_read 读取记忆，调研事实须经专用工具复核。",
            }
        except (FileNotFoundError, OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
            return self._local_fallback(query.strip(), module=module)

    def _environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update(
            {
                "BASIC_MEMORY_CONFIG_DIR": str(self.config_dir),
                "BASIC_MEMORY_MCP_PROJECT": BASIC_MEMORY_PROJECT,
                "BASIC_MEMORY_NO_PROMOS": "1",
                "BASIC_MEMORY_LOG_LEVEL": "ERROR",
                "BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED": "false",
                "BASIC_MEMORY_DEFAULT_SEARCH_TYPE": "text",
                # 索引只读：禁止上游同步为事实源补写 frontmatter 或 permalink。
                "BASIC_MEMORY_ENSURE_FRONTMATTER_ON_SYNC": "false",
                "BASIC_MEMORY_DISABLE_PERMALINKS": "true",
            }
        )
        return environment

    def _validate_paths(self) -> None:
        # CLI 会递归索引，不能只检查普通记忆工具的白名单文件。
        for root in (self.memory_root, self.config_dir):
            if root.is_symlink() or any(path.is_symlink() for path in root.rglob("*")):
                raise ValueError("记忆与索引目录不允许使用符号链接")
        for name in MEMORY_FILES:
            WorkspaceMemory(self.workspace)._paths(name)
        module_memory_files(self.workspace)

    def _filter_results(self, results: list[Any], module: str | None) -> list[dict[str, Any]]:
        normalized = []
        for result in results:
            if not isinstance(result, dict):
                continue
            if module is None:
                normalized.append(result)
                continue
            raw_path = next((result.get(key) for key in ("path", "file_path", "filepath", "name")
                             if isinstance(result.get(key), str)), "")
            path = Path(raw_path)
            if path.is_absolute():
                try:
                    relative = path.resolve().relative_to(self.memory_root.resolve())
                except ValueError:
                    continue
            else:
                relative = path
            parts = relative.parts
            if len(parts) >= 2 and parts[0] == "modules" and parts[1] == module:
                normalized.append({**result, "module": module})
        return normalized

    def _run(self, args: list[str]) -> str:
        completed = subprocess.run(
            [self.command, *args],
            cwd=self.workspace,
            env=self._environment(),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if completed.returncode != 0:
            raise subprocess.CalledProcessError(
                completed.returncode,
                [self.command, *args],
                output=completed.stdout,
                stderr=completed.stderr,
            )
        return completed.stdout

    def _ensure_project(self) -> None:
        config_file = self.config_dir / "config.json"
        if config_file.is_file():
            try:
                config = json.loads(config_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                config = {}
            if self._configured_path(config) == self.memory_root:
                return
        self.config_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._run(
                [
                    "project",
                    "add",
                    BASIC_MEMORY_PROJECT,
                    str(self.memory_root),
                    "--local",
                    "--default",
                ]
            )
        except subprocess.CalledProcessError as exc:
            # Basic Memory reports an existing project as an error. Reuse it only when
            # the persisted config proves it points to this exact workspace directory.
            if not self._project_is_configured():
                raise exc

    def _project_is_configured(self) -> bool:
        config_file = self.config_dir / "config.json"
        if not config_file.is_file():
            return False
        try:
            config = json.loads(config_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return self._configured_path(config) == self.memory_root

    @staticmethod
    def _configured_path(config: Any) -> Path | None:
        if not isinstance(config, dict) or not isinstance(config.get("projects", {}), dict):
            raise ValueError("Basic Memory 配置结构无效")
        entry = config.get("projects", {}).get(BASIC_MEMORY_PROJECT)
        if entry is None:
            return None
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not entry["path"]:
            raise ValueError("Basic Memory 项目路径无效")
        return Path(entry["path"]).resolve()

    def _local_fallback(self, query: str, *, module: str | None = None) -> dict[str, Any]:
        terms = [term.casefold() for term in query.split() if term.strip()]
        results: list[dict[str, Any]] = []
        total_matches = 0
        read_full_files = []
        sources = []
        if module is None:
            sources.extend((name, WorkspaceMemory(self.workspace).read(name), None)
                           for name in sorted(MEMORY_FILES))
        for path in module_memory_files(self.workspace, module):
            sources.append((str(path.relative_to(self.memory_root)), path.read_text(encoding="utf-8"), module))
        for name, content, module_name in sources:
            matches = [
                line.strip()
                for line in content.splitlines()
                if all(term in line.casefold() for term in terms)
            ]
            if matches:
                total_matches += len(matches)
                if len(matches) > 20:
                    read_full_files.append(name)
                result = {"name": name, "matches": matches[:20]}
                if module_name is not None:
                    result["module"] = module_name
                results.append(result)
        return {
            "backend": "local-fallback",
            "available": False,
            "expected_version": BASIC_MEMORY_VERSION,
            "project": BASIC_MEMORY_PROJECT,
            "results": results,
            "has_more": bool(read_full_files),
            "total_matches": total_matches,
            "read_full_files": read_full_files,
            "completeness_note": "每份文件最多展示 20 条匹配；使用 dy_memory_read 或 dy_module_memory_read 读取完整内容，调研事实须经专用工具复核。",
        }
