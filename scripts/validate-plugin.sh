#!/usr/bin/env bash
# 使用官方 Codex CLI，在临时 CODEX_HOME 中验证市场安装。
set -euo pipefail

dy_repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

for dy_tool in codex uv; do
    if ! command -v "$dy_tool" >/dev/null 2>&1; then
        printf '插件校验失败：缺少 %s，请安装并加入 PATH。\n' "$dy_tool" >&2
        exit 1
    fi
done

codex --version
cd -- "$dy_repo_root"
exec uv run --frozen --project plugins/dy_superpowers/mcp-server --extra test \
    pytest -q tests/test_marketplace_layout.py tests/test_codex_install.py \
    plugins/dy_superpowers/mcp-server/tests/test_session_initialize.py::test_plugin_session_start_hook_targets_registered_mcp_tool \
    plugins/dy_superpowers/mcp-server/tests/test_stdio_protocol.py --tb=short
