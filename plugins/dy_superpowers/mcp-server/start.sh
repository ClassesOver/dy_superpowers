#!/usr/bin/env bash
# 从脚本位置定位插件，保留宿主传入的项目工作目录。
set -euo pipefail
dy_mcp_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec uv run --frozen --project "$dy_mcp_root" python "$dy_mcp_root/src/server.py" "$@"
