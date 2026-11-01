"""Workspace-scoped project and module memory for dy_superpowers."""

import json
import re
from datetime import datetime, timezone
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
NOTE_SLUG = re.compile(r"^[a-z0-9][a-z0-9/-]{0,120}$")  # 允许 / 形成项目目录分层


def note_path(workspace: str | Path, slug: str) -> Path:
    """wiki 记忆页位于 .dy_superpowers/notes/，纳入 Basic Memory 索引范围。"""
    workspace_path = Path(workspace).resolve()
    if not isinstance(slug, str) or NOTE_SLUG.fullmatch(slug) is None:
        raise ValueError("note 标识必须是小写字母、数字或连字符（最多 81 字符）")
    raw_root = workspace_path / ".dy_superpowers"
    raw_notes = raw_root / "notes"
    if any(path.is_symlink() for path in (raw_root, raw_notes)):
        raise ValueError("wiki 记忆目录不允许使用符号链接")
    notes = raw_notes.resolve()
    if not notes.is_relative_to(workspace_path.resolve()):
        raise ValueError("wiki 记忆必须位于当前工作区")
    path = notes / f"{slug}.md"
    if any(parent.is_symlink() for parent in path.parents if parent != notes):
        raise ValueError("wiki 记忆父目录不允许使用符号链接")
    if path.is_symlink() or not path.resolve().is_relative_to(notes):
        raise ValueError("wiki 记忆文件必须位于记忆目录内")
    return path


def write_note(workspace: str | Path, slug: str, title: str, body: str, tags: list[str],
               *, confirmed: bool = False, source_refs: list[str] | None = None,
               module: str | None = None, research_topic: bool = False,
               expected_content: str | None = None) -> Path:
    if confirmed is not True:
        raise PermissionError("wiki 记忆写入需要用户明确确认")
    path = note_path(workspace, slug)
    if not isinstance(title, str) or not title.strip() or len(title) > 120:
        raise ValueError("note 标题必须是非空文本（≤120 字符）")
    if not isinstance(body, str) or not body.strip():
        raise ValueError("note 正文必须是非空文本")
    if not isinstance(research_topic, bool):
        raise ValueError("research_topic 必须是布尔值")
    if module is not None or research_topic:
        module = validate_module(module)
    if expected_content is not None and not isinstance(expected_content, str):
        raise ValueError("expected_content 必须是读取时的完整原文")
    tags = [str(tag).strip() for tag in (tags or []) if str(tag).strip()][:10]
    source_refs = [str(ref).strip() for ref in (source_refs or []) if str(ref).strip()][:20]
    # JSON 字符串与数组也是合法 YAML，避免用户文本改变 frontmatter 结构或值类型。
    frontmatter = ["---", f"title: {json.dumps(title.replace(chr(10), ' '), ensure_ascii=False)}", "type: note",
                   f"tags: {json.dumps(tags, ensure_ascii=False)}",
                   f"module: {json.dumps(module, ensure_ascii=False)}",
                   f"research_topic: {json.dumps(research_topic)}", "confirmed: true",
                   f"confirmed_at: {json.dumps(datetime.now(timezone.utc).isoformat())}",
                   f"source_refs: {json.dumps(source_refs, ensure_ascii=False)}", "---", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    with record_lock(path):
        exists = path.exists()
        current = path.read_text(encoding="utf-8") if exists else ""
        existing_topic = False
        if current.startswith("---\n") and "\n---\n" in current[4:]:
            header = current[4:].split("\n---\n", 1)[0]
            existing_topic = 'research_topic: true' in header.splitlines()
        if exists and (research_topic or existing_topic) and expected_content is None:
            raise ValueError("更新已有主题笔记必须提供读取时的 expected_content 完整原文")
        if expected_content is not None and expected_content != current:
            raise ValueError("主题笔记更新冲突，请重新读取并合并，再确认写入")
        if not exists and expected_content:
            raise ValueError("创建主题笔记时 expected_content 必须为空")
        atomic_write(path, "\n".join(frontmatter) + body.rstrip() + "\n")
    return path


def read_note(workspace: str | Path, slug: str) -> str:
    path = note_path(workspace, slug)
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def list_notes(workspace: str | Path) -> list[str]:
    notes = Path(workspace).resolve() / ".dy_superpowers" / "notes"
    if not notes.is_dir() or notes.is_symlink() or notes.parent.is_symlink():
        return []
    return sorted(p.relative_to(notes).with_suffix("").as_posix() for p in notes.rglob("*.md")
                  if p.is_file() and not p.is_symlink()
                  and not any(parent.is_symlink() for parent in p.parents if parent != notes)
                  and NOTE_SLUG.fullmatch(p.relative_to(notes).with_suffix("").as_posix()))


def validate_module(module: str) -> str:
    if not isinstance(module, str) or MODULE_PATTERN.fullmatch(module) is None:
        raise ValueError("module 必须是小写字母、数字、下划线或连字符组成的标识")
    return module


def module_memory_path(workspace: str | Path, module: str) -> Path:
    workspace_path = Path(workspace).resolve()
    module = validate_module(module)
    raw_memory_root = workspace_path / ".dy_superpowers"
    raw_modules_dir = raw_memory_root / "modules"
    raw_module_dir = raw_modules_dir / module
    if any(path.is_symlink() for path in (raw_memory_root, raw_modules_dir, raw_module_dir)):
        raise ValueError("模块记忆目录不允许使用符号链接")
    memory_root = raw_memory_root.resolve()
    module_dir = raw_module_dir.resolve()
    if not memory_root.is_relative_to(workspace_path) or not module_dir.is_relative_to(memory_root):
        raise ValueError("模块记忆必须位于当前工作区")
    research_path = module_dir / "research.md"
    if research_path.is_symlink() or not research_path.resolve().is_relative_to(memory_root):
        raise ValueError("模块记忆文件必须位于当前工作区且不能使用符号链接")
    return research_path


def module_memory_files(workspace: str | Path, module: str | None = None) -> list[Path]:
    workspace_path = Path(workspace).resolve()
    raw_root = workspace_path / ".dy_superpowers" / "modules"
    if workspace_path.joinpath(".dy_superpowers").is_symlink() or raw_root.is_symlink():
        raise ValueError("模块记忆目录必须位于当前工作区且不能使用符号链接")
    root = raw_root.resolve()
    if not root.exists():
        return []
    if root.is_symlink() or not root.is_relative_to(workspace_path):
        raise ValueError("模块记忆目录必须位于当前工作区")
    modules = [validate_module(module)] if module is not None else [
        path.name for path in sorted(root.iterdir())
        if path.is_dir() and not path.is_symlink() and MODULE_PATTERN.fullmatch(path.name)
    ]
    paths = []
    for name in modules:
        path = module_memory_path(workspace_path, name)
        if path.is_file() and not path.is_symlink():
            paths.append(path)
    return paths


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
