#!/usr/bin/env python3
"""校验语义包的结构、绑定版本和向量完整性。"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / 'plugins/dy_superpowers/mcp-server/src'))
from dy_superpowers_mcp.semantic import SemanticIndex


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('database', type=Path)
    parser.add_argument('--bundle-id')
    args = parser.parse_args()
    index = SemanticIndex(args.database, bundle_id=args.bundle_id)
    count = index.db.execute('select count(*) from chunks').fetchone()[0]
    if not count:
        raise SystemExit('语义索引没有可向量化片段')
    print(f'语义索引有效：{count} 个片段，format={index.metadata["format"]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
