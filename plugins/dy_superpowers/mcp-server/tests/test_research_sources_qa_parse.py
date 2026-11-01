"""调研来源解析：问答文本块与普通清单文本的候选提取。"""
import importlib.util
import sys
from pathlib import Path

PLUGIN = Path(__file__).parents[2]
sys.path.insert(0, str(PLUGIN / "mcp-server/src"))



# parse_plain 解析 .txt：标题行给检查顺序，正文行“01｜结论”给子项编号。
V1 = ["# 1 应用安装", "01｜费用基础应用需要安装并启用。", "02｜OCR 模块按需启用，服务已确认可用。"]
V2 = ["# 1 应用安装", "01｜费用基础应用需要安装，并在验收前启用。",  # 01 结论变更
      "# 999 不存在的检查项", "01｜该行缺少有效键时应阻塞。"]  # 02 被移除；第二条将在测试中去掉键


def _write_source(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _load_sources():
    spec = importlib.util.spec_from_file_location(
        "research_sources", PLUGIN / "skills/module-research/scripts/research_sources.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rs = _load_sources()


def test_qa_text_blocks_parse(tmp_path):
    qa_md = tmp_path / "qa.md"
    qa_md.write_text("问：总账期间是否已启用？\n答：2026 年度期间已开启。\n"
                     "附件：https://gitlab.example.com/period.png\n\n"
                     "问：科目结构数量？\n答：共 3 套结构。\n", encoding="utf-8")
    parsed = rs.parse_source(qa_md, "general-ledger")
    assert parsed["coverage"]["source_subitems"] == 2
    first = parsed["records"][0]
    assert first["locator"]["kind"] == "qa" and first["check_order"] == ""
    assert first["suggested_evidence"] == "https://gitlab.example.com/period.png"
    assert parsed["records"][1]["conclusion"] == "共 3 套结构。"
    # 普通清单文本（无问答结构）不受 QA 模式影响
    plain = tmp_path / "plain.txt"
    plain.write_text("\n".join(V1) + "\n", encoding="utf-8")
    rows = rs.parse_source(plain, "expense")["records"]
    assert rows[0]["research_id"] == "01" and rows[0]["check_order"] == "1"
