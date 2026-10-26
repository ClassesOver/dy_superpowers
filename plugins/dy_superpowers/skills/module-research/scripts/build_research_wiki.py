"""构建项目调研 Wiki；必须先确认稳定且唯一的项目身份，不猜账套名称。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "mcp-server/src"))
from dy_superpowers_mcp.project_wiki import build_bundle, stage_wiki


def build_pages(workspace):
    return build_bundle(workspace)["pages"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--stage", action="store_true", help="构建并记录本项目页面发布清单")
    parser.add_argument("--output-dir", type=Path, help="仅预览导出；发布使用 --stage")
    args = parser.parse_args()
    bundle = build_bundle(args.workspace)
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for slug, content in bundle["pages"].items():
            target = args.output_dir / (slug + ".md")
            if target.exists():
                raise ValueError("预览目录有同名文件，拒绝覆盖")
            target.write_text(content, encoding="utf-8")
    if args.stage:
        stage_wiki(args.workspace)
    print(json.dumps({"project_name": bundle["project"]["name"],
                      "pages": len(bundle["pages"]), "overview_slug": bundle["overview_slug"],
                      "staged": args.stage}, ensure_ascii=False))


if __name__ == "__main__":
    main()
