"""安装即用：工作区 .env 白名单自动加载回归。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import server


def test_env_loader_reads_whitelist_without_override(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".env").write_text(
        "# 连接配置\n"
        "APP_URL=http://erp.example.com\n"
        "export DB_NAME=vali5\n"
        "APP_USER=reader\n"
        "APP_PASSWORD='pw'\n"
        "DB_HOST=db.example.com\n"
        "DB_PORT=5432\n"
        "DB_USER=db-reader\n"
        "DB_PASSWORD='db-pw'\n"
        "DY_KNOWLEDGE_EMBEDDING_TOKEN=\"pat-secret\"\n"
        "NOT_ALLOWED_KEY=whatever\n"
        "DY_KNOWLEDGE_EMBEDDING_URL=\n",
        encoding="utf-8")
    for key in ("APP_URL", "DB_NAME", "APP_USER", "APP_PASSWORD", "DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DY_KNOWLEDGE_EMBEDDING_TOKEN", "DY_KNOWLEDGE_EMBEDDING_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DB_NAME", "existing-db")

    loaded = server.load_workspace_env(workspace)
    assert loaded == ["APP_URL", "APP_USER", "APP_PASSWORD", "DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DY_KNOWLEDGE_EMBEDDING_TOKEN"]  # 按文件行序；空值不加载
    import os
    assert os.environ["APP_URL"] == "http://erp.example.com"
    assert os.environ["DB_NAME"] == "existing-db"  # 已设置的环境变量优先
    assert os.environ["DY_KNOWLEDGE_EMBEDDING_TOKEN"] == "pat-secret"
    assert os.environ["APP_PASSWORD"] == "pw"
    assert os.environ["DB_HOST"] == "db.example.com"
    assert os.environ["DB_PORT"] == "5432"
    assert os.environ["DB_USER"] == "db-reader"
    assert os.environ["DB_PASSWORD"] == "db-pw"
    assert "NOT_ALLOWED_KEY" not in os.environ  # 白名单外不加载


def test_env_loader_ignores_missing_and_symlink(tmp_path):
    assert server.load_workspace_env(tmp_path) == []  # 无 .env
    (tmp_path / ".env").symlink_to(tmp_path / "elsewhere")
    assert server.load_workspace_env(tmp_path) == []  # 符号链接拒绝
