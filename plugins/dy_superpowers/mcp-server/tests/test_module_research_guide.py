from pathlib import Path
import re


ROOT = Path(__file__).parents[2]
SKILL = ROOT / "skills/module-research/SKILL.md"
GUIDE = ROOT / "skills/module-research/references/general-ledger-research-guide.md"


def test_module_research_skill_contains_the_built_in_general_ledger_qa():
    skill = SKILL.read_text(encoding="utf-8")

    assert "## 总账内置 QA" in skill
    assert "每个配置项先提出一个汇总问题" in skill
    assert "EXP-1-001" in skill and "EXP-12-037" in skill
    assert "本期交付哪些公司的总账模块" in skill
    assert "凭证是否需要打印" in skill
    assert "当前会话" in skill
    assert "多轮" in skill


def test_general_ledger_guide_maps_all_check_items_and_attachment_templates():
    guide = GUIDE.read_text(encoding="utf-8")

    check_ids = set(re.findall(r"^\| (EXP-[0-9]+(?:-[0-9]+)+) \|", guide, flags=re.MULTILINE))
    templates = re.findall(r"^### 模板：(.+)$", guide, flags=re.MULTILINE)
    assert len(check_ids) == 37
    assert "EXP-9-031" in check_ids and "EXP-12-037" in check_ids
    assert len(templates) == 38
    assert "公司应用清单" in templates
    assert "通用转账模板清单" in templates
    assert "提取指引要素" in guide
    assert "附件（按需）" in guide
    assert "附件证据不得扩展业务确认范围" in guide


def test_research_wiki_uses_confirmed_findings_and_template_source_index():
    skill = SKILL.read_text(encoding="utf-8")

    assert "dy_wiki_build" in skill
    assert "已确认结论" in skill
    assert "资料索引" in skill
    assert "不得发布附件全文" in skill


def test_attachment_locations_use_supported_evidence_ref_field():
    guide = GUIDE.read_text(encoding="utf-8")

    assert "`attachment_refs`" not in guide
    assert "附件定位写入 `evidence_ref`" in guide


def _expense_matrix_subitems():
    matrix = ROOT / "skills/expense-inspection/references/inspection-matrix.md"
    order = None
    items = {}
    for line in matrix.read_text(encoding="utf-8").splitlines():
        if line.startswith("## 检查顺序 "):
            order = line.removeprefix("## 检查顺序 ").strip()
        elif match := re.match(r"^\|\s*(\d+)｜([^|]+)\|", line):
            items[order, match.group(1)] = match.group(2).strip()
    return items


def test_expense_guide_covers_current_matrix_subitems_and_source_mapping():
    guide = ROOT / "skills/module-research/references/expense-research-guide.md"
    assert guide.is_file(), "费用调研缺少来源可追溯的内置QA参考"
    text = guide.read_text(encoding="utf-8")
    rows = re.findall(r"^\| expense \| (\d+) \| (\d+) \| (.*?) \| [^|]+ \| 费用调研清单!G(\d+) \| 费用模块巡检Skill!K(\d+) \| [^|]+ \|$", text, re.MULTILINE)
    mapped = {}
    for order, subitem, extraction, conclusion_row, acceptance_row in rows:
        assert int(conclusion_row) == int(acceptance_row) + 2
        assert (order, subitem) not in mapped
        mapped[order, subitem] = extraction
    matrix = _expense_matrix_subitems()
    assert len(mapped) == len(matrix) == 92
    assert mapped == matrix
    assert "费用模块巡检Skill手册_V1.1.xlsx" in text
    assert "费用模块巡检Skill!A1:K29" in text
    assert "费用调研清单!A3:G95" in text
    assert "V1.2" not in text
    assert "费用模块调研结果.xlsx" in text
    assert "92 个" in text and "28 个" in text


def test_expense_guidance_routes_qa_and_preserves_material_boundary():
    text = (ROOT / "skills/module-research/references/expense-research-guide.md").read_text(encoding="utf-8")
    skill = SKILL.read_text(encoding="utf-8")
    inspection = (ROOT / "skills/expense-inspection/SKILL.md").read_text(encoding="utf-8")
    assert "references/expense-research-guide.md" in skill
    assert "expense-research-guide.md" in inspection
    assert "独立空白附件模板" in text
    assert "不是空白附件模板" in text
    assert not re.findall(r"^### 模板：", text, re.MULTILINE)
    assert "scope_id" in text and "expected_revision" in text
    assert "只记录待办问题" in text and "不保存候选答案" in text
    assert "确认后写入" in text and "evidence_ref" in text
    assert "模板示例" in text and "附件观察" in text


def test_expense_source_generic_carrier_subitem_has_exact_matrix_mapping():
    text = (ROOT / "skills/module-research/references/expense-research-guide.md").read_text(encoding="utf-8")
    matrix = _expense_matrix_subitems()
    assert ("28", "03") in matrix, "结果G95及K93的有效来源子项未进入矩阵"
    assert matrix["28", "03"] == "通用承运人映射：允许／不允许；适用范围【待确认】"
    source = (ROOT / "skills/expense-inspection/references/inspection-matrix.md").read_text(encoding="utf-8")
    assert "03｜仅在确认需要席别映射时检查通用承运人范围；允许或不允许的口径与实际匹配结果一致。" in source
    assert "费用调研清单!G95" in text and "费用模块巡检Skill!K93" in text
    assert "手册调研清单!F29" not in text
    assert "没有对应28/03键" not in text



def test_expense_source_roles_and_actual_workbook_inventory():
    text = (ROOT / "skills/module-research/references/expense-research-guide.md").read_text(encoding="utf-8")
    assert "一般巡检参考" in text
    assert "不含调研清单" in text
    assert "92 条已填写项目要求" in text
    assert "归一化收集要素" in text
    assert "不是 V1.1 手册原文" in text
    assert "J2:J93" in text and "G4:G95" in text
    assert "均可见" in text and "无隐藏行列" in text
    sheets = re.findall(r"^\| (0[1-9]_[^|]+|1[0-2]_[^|]+) \| (\d+)×(\d+) \| (\d+) \|", text, re.MULTILINE)
    assert len(sheets) == 12
    assert ("05_城市与季节", "435", "8", "4") in sheets
    assert ("06_费用政策", "963", "11", "6") in sheets
    groups = re.findall(r"^\| DY-(\d+) \| (\d+) \| ([^|]+) \| ([^|]+) \| ([^|]+) \| ([^|]+) \| A(\d+):F(\d+) \|$", text, re.MULTILINE)
    assert len(groups) == 28
    assert [int(row[0]) for row in groups] == list(range(1, 29))
    assert groups[0][-2:] == ("4", "9")
    assert groups[-1][-2:] == ("93", "95")


def test_expense_attachment_catalog_preserves_actual_headers_and_data_ranges():
    text = (ROOT / "skills/module-research/references/expense-research-guide.md").read_text(encoding="utf-8")
    sections = re.findall(r"^\| (\d{2}_[^|]+) \| A\d+：[^|]+ \| 第\d+行 \| ([^|]+) \| ([A-Z]+\d+:[A-Z]+\d+) \| (\d+) \|$", text, re.MULTILINE)
    assert len(sections) == 26
    assert sum(int(row[3]) for row in sections) == 1564
    catalogs = {(row[0], row[2]): row[1] for row in sections}
    assert catalogs["05_城市与季节", "A8:E435"] == "城市、编码、有效性要求、淡季月份、旺季月份"
    assert catalogs["06_费用政策", "A14:K963"].endswith("失效日期、职称")
    assert catalogs["12_OCR错误提示", "A8:D71"] == "错误码、错误标题、用户提示、详细说明"
    assert "不是空白附件模板" in text
    assert "公司、编码、上级公司、本位币" in text
    assert "1022" not in text and "XXX第一医院" not in text
    assert "2023-03-22" not in text
