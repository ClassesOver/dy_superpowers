"""Workspace-scoped project memory for dy_superpowers."""

from pathlib import Path

MEMORY_FILES = {
    "preferences.md",
    "profile.md",
    "decisions.md",
    "delivery-register.md",
    "skill-candidates.md",
    "module-research.md",
}

WRITABLE_MEMORY_FILES = MEMORY_FILES - {"module-research.md"}


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

    def write(self, name: str, content: str, *, confirmed: bool = False) -> Path:
        if confirmed is not True:
            raise PermissionError("memory updates require explicit user confirmation")
        if not isinstance(content, str):
            raise TypeError("memory content must be text")
        if name not in WRITABLE_MEMORY_FILES:
            raise ValueError("module-research.md must be updated through the research record tools")
        directory, path = self._paths(name)
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path
