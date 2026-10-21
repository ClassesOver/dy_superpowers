"""Optional Basic Memory search adapter for the existing dy_superpowers MCP server."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .memory import MEMORY_FILES, WorkspaceMemory


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
        self.command = command or os.environ.get("DY_BASIC_MEMORY_COMMAND", "basic-memory")

    def search(self, query: str) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("搜索关键词必须是非空文本")
        query = query.strip()
        if query.startswith("-"):
            # 查询词会原样作为 argv 传给 basic-memory CLI，前导连字符会被当作选项解析。
            raise ValueError("搜索关键词不能以连字符开头")
        self._validate_paths()
        try:
            self._ensure_project()
            self._run(["reindex", "--search", "--project", BASIC_MEMORY_PROJECT])
            output = self._run(
                [
                    "tool",
                    "search-notes",
                    query,
                    "--json",
                    "--local",
                    "--project",
                    BASIC_MEMORY_PROJECT,
                    "--page-size",
                    "20",
                ]
            )
            payload = json.loads(output)
            results = payload.get("results") if isinstance(payload, dict) else payload
            if not isinstance(results, list):
                raise ValueError("Basic Memory 返回了无效搜索结果")
            return {
                "backend": "basic-memory",
                "available": True,
                "expected_version": BASIC_MEMORY_VERSION,
                "project": BASIC_MEMORY_PROJECT,
                "results": results,
            }
        except (FileNotFoundError, OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
            return self._local_fallback(query.strip())

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
            entry = config.get("projects", {}).get(BASIC_MEMORY_PROJECT, {})
            if isinstance(entry, dict) and Path(entry.get("path", "")).resolve() == self.memory_root:
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
        entry = config.get("projects", {}).get(BASIC_MEMORY_PROJECT, {})
        return isinstance(entry, dict) and Path(entry.get("path", "")).resolve() == self.memory_root

    def _local_fallback(self, query: str) -> dict[str, Any]:
        terms = [term.casefold() for term in query.split() if term.strip()]
        results: list[dict[str, Any]] = []
        for name in sorted(MEMORY_FILES):
            content = WorkspaceMemory(self.workspace).read(name)
            matches = [
                line.strip()
                for line in content.splitlines()
                if all(term in line.casefold() for term in terms)
            ]
            if matches:
                results.append({"name": name, "matches": matches[:20]})
        return {
            "backend": "local-fallback",
            "available": False,
            "expected_version": BASIC_MEMORY_VERSION,
            "project": BASIC_MEMORY_PROJECT,
            "results": results,
        }
