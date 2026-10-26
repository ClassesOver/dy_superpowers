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
        "ODOO_URL=http://erp.example.com\n"
        "export ODOO_DB=vali5\n"
        "DY_WIKI_TOKEN=\"pat-secret\"\n"
        "ODOO_PASSWORD='pw'\n"
        "NOT_ALLOWED_KEY=whatever\n"
        "DY_WIKI_API_URL=\n",
        encoding="utf-8")
    for key in ("ODOO_URL", "ODOO_DB", "ODOO_PASSWORD", "DY_WIKI_TOKEN", "DY_WIKI_API_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ODOO_DB", "existing-db")

    loaded = server.load_workspace_env(workspace)
    assert loaded == ["ODOO_URL", "DY_WIKI_TOKEN", "ODOO_PASSWORD"]  # 按文件行序；空值不加载
    import os
    assert os.environ["ODOO_URL"] == "http://erp.example.com"
    assert os.environ["ODOO_DB"] == "existing-db"  # 已设置的环境变量优先
    assert os.environ["DY_WIKI_TOKEN"] == "pat-secret"
    assert os.environ["ODOO_PASSWORD"] == "pw"
    assert "NOT_ALLOWED_KEY" not in os.environ  # 白名单外不加载


def test_env_loader_ignores_missing_and_symlink(tmp_path):
    assert server.load_workspace_env(tmp_path) == []  # 无 .env
    (tmp_path / ".env").symlink_to(tmp_path / "elsewhere")
    assert server.load_workspace_env(tmp_path) == []  # 符号链接拒绝
