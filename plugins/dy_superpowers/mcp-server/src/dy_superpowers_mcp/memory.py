"""Workspace-scoped project and module memory for dy_superpowers."""

import re
from pathlib import Path

from .storage import atomic_write, record_lock

# 项目记忆文件的唯一清单；顺序即会话索引的展示顺序。
MEMORY_FILES = (
    "preferences.md",
    "profile.md",
    "decisions.md",
    "delivery-register.md",
    "skill-candidates.md",
)

WRITABLE_MEMORY_FILES = MEMORY_FILES
MODULE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def validate_module(module: str) -> str:
    if not isinstance(module, str) or MODULE_PATTERN.fullmatch(module) is None:
        raise ValueError("module 必须是小写字母、数字、下划线或连字符组成的标识")
    return module


class WorkspaceMemory:
    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).resolve()

    def _paths(self, name: str) -> tuple[Path, Path]:
        if name not in MEMORY_FILES:
            raise ValueError("unsupported memory file")
        directory = self.workspace / ".dy_superpowers"
        resolved_directory = directory.resolve()
        if not resolved_directory.is_relative_to(self.workspace):
            raise ValueError("memory directory must stay inside the workspace")
        path = resolved_directory / name
        if not path.resolve().is_relative_to(resolved_directory):
            raise ValueError("memory file must stay inside the workspace memory directory")
        return resolved_directory, path

    def read(self, name: str) -> str:
        directory, path = self._paths(name)
        if not directory.exists() or not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")

    def write(self, name: str, content: str, *, confirmed: bool = False, expected_content: str | None = None) -> Path:
        if confirmed is not True:
            raise PermissionError("memory updates require explicit user confirmation")
        if not isinstance(content, str):
            raise TypeError("memory content must be text")
        directory, path = self._paths(name)
        directory.mkdir(parents=True, exist_ok=True)
        with record_lock(path):
            current = self.read(name)
            if path.exists() and expected_content is None:
                raise ValueError("更新已有记忆必须提供读取时的 expected_content 原文")
            if expected_content is not None and current != expected_content:
                raise ValueError("记忆更新冲突，请重新读取并合并，再确认写入")
            atomic_write(path, content)
        return path
