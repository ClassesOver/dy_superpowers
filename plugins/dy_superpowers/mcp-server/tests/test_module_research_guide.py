from pathlib import Path
import re


ROOT = Path(__file__).parents[2]
SKILL = ROOT / "skills/module-research/SKILL.md"
GUIDE = ROOT / "skills/module-research/references/general-ledger-research-guide.md"


def test_module_research_skill_contains_the_built_in_general_ledger_qa():
    skill = SKILL.read_text(encoding="utf-8")

    assert "## 总账内置 QA" in skill
    assert "每个配置项先提出一个汇总问题" in skill
    assert "当前矩阵中的 `GL-*` 编号" in skill
    assert "详细问题、字段和缺口映射" in skill
    assert "当前会话" in skill
    assert "多轮" in skill


def test_general_ledger_guide_maps_all_check_items_and_attachment_templates():
    guide = GUIDE.read_text(encoding="utf-8")

    check_ids = set(re.findall(r"^\| (GL-[0-9]+(?:-[0-9]+)+) \|", guide, flags=re.MULTILINE))
    templates = re.findall(r"^### 模板：(.+)$", guide, flags=re.MULTILINE)
    assert len(check_ids) == 37
    assert "GL-9-031" in check_ids and "GL-12-037" in check_ids
    assert len(templates) == 39
    assert "凭证打印样张及要求" in templates
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
            if match := re.fullmatch(r"EXP-(\d{3})", order):
                order = str(int(match.group(1)))
        elif match := re.match(r"^\|\s*(\d+)｜([^|]+)\|", line):
            items[order, match.group(1)] = match.group(2).strip()
    return items


def test_expense_guide_covers_current_matrix_subitems_and_source_mapping():
    guide = ROOT / "skills/module-research/references/expense-research-guide.md"
    assert guide.is_file(), "费用调研缺少来源可追溯的内置QA参考"
    text = guide.read_text(encoding="utf-8")
    rows = re.findall(r"^\| expense \| EXP-(\d{3}) \| (\d+) \| (.*?) \| ([^|]+) \| 仅保存已确认事实", text, re.MULTILINE)
    mapped = {}
    for order, subitem, extraction, _attachment in rows:
        key = (str(int(order.removeprefix("EXP-"))), subitem)
        assert key not in mapped
        mapped[key] = extraction
    matrix = _expense_matrix_subitems()
    assert len(mapped) == len(matrix) == 92
    assert all(mapped[key].startswith(matrix[key]) for key in matrix)
    assert "费用模块巡检Skill手册_V1.1.xlsx" not in text
    assert "费用模块调研更新_2.0.xlsx" not in text
    assert "矩阵第7组为借款与核销补问项" in text
    assert "EXP-028 |" in text and "EXP-028-03" in text
    source_section = text.split("### 逐点提取 QA", 1)[1].split("## 提取要素与矩阵映射", 1)[0]
    source_rows = re.findall(r"^\| EXP-\d{3} \| [^|]+ \| .+ \| .+ \| .+ \|$", source_section, re.MULTILINE)
    assert len(source_rows) == 64
    assert "检查顺序显示为 `EXP-001`" in text


def test_expense_guidance_routes_qa_and_preserves_material_boundary():
    text = (ROOT / "skills/module-research/references/expense-research-guide.md").read_text(encoding="utf-8")
    skill = SKILL.read_text(encoding="utf-8")
    inspection = (ROOT / "skills/expense-inspection/SKILL.md").read_text(encoding="utf-8")
    assert "references/expense-research-guide.md" in skill
    assert "expense-research-guide.md" in inspection
    assert "附件是调研收集模板，不是系统现状证明" in text
    assert len(re.findall(r"^### 模板：", text, re.MULTILINE)) == 19
    assert "scope_id" in text and "expected_revision" in text
    assert "业务明确确认" in text and "evidence_ref" in text
    assert "模板示例" in text and "历史观察" in text


def test_expense_source_generic_carrier_subitem_has_exact_matrix_mapping():
    text = (ROOT / "skills/module-research/references/expense-research-guide.md").read_text(encoding="utf-8")
    matrix = _expense_matrix_subitems()
    assert ("28", "03") in matrix, "结果G95及K93的有效来源子项未进入矩阵"
    assert matrix["28", "03"] == "通用承运人映射：允许／不允许；适用范围【待确认】"
    source = (ROOT / "skills/expense-inspection/references/inspection-matrix.md").read_text(encoding="utf-8")
    assert "03｜仅在确认需要席别映射时检查通用承运人范围；允许或不允许的口径与实际匹配结果一致。" in source
    assert "通用承运人映射" in text


def test_imported_pending_business_signoffs_must_trigger_native_qa_before_finalizing():
    skill = SKILL.read_text(encoding="utf-8")
    source_intake = (SKILL.parent / "references/source-intake.md").read_text(encoding="utf-8")

    assert "request_user_input" in skill
    assert "待业务签认" in skill
    assert "待确认" in skill
    assert "不能只生成候选台账后结束" in skill
    assert "每组最多 3 个相互独立的问题" in skill
    assert "解析不能作为最终回复结束" in source_intake
    assert "不能只列“待业务签认”后结束" in source_intake
    assert "项目档案生成不适用此自动 QA 规则" in source_intake


def test_research_candidate_ledger_output_is_grouped_and_scannable():
    skill = SKILL.read_text(encoding="utf-8")

    assert "不要把大量长结论放入单张宽表" in skill
    assert "按配置组分节" in skill
    assert "先展示待确认与待业务签认项" in skill
    assert "逐条使用短标签换行展示" in skill



def test_expense_source_roles_and_actual_workbook_inventory():
    text = (ROOT / "skills/module-research/references/expense-research-guide.md").read_text(encoding="utf-8")
    assert "矩阵第7组为借款与核销补问项" in text
    assert len(re.findall(r"^\| (?:0[1-9]|1[0-9])_[^|]+ \|", text, re.MULTILINE)) == 19
    assert "来源与覆盖" not in text
    assert "来源定位" not in text


def test_expense_attachment_catalog_preserves_actual_headers_and_data_ranges():
    text = (ROOT / "skills/module-research/references/expense-research-guide.md").read_text(encoding="utf-8")
    assert "17_单据页面字段明细" in text
    assert "18_费用项目字段明细" in text
    assert "19_申请项目字段明细" in text
    assert "页面名称；页面环节；适用公司；适用单据类型" in text
    assert "配置名称；适用公司；有效状态；附件类型；敏感词" in text
    assert "配置名称；适用公司；信用说明；有效状态；初始分；满分" in text
    assert "公司、编码、上级公司、本位币" not in text
    assert "1022" not in text and "XXX第一医院" not in text
    assert "2023-03-22" not in text


def test_expense_field_qa_maps_to_the_actual_field_templates():
    text = (ROOT / "skills/module-research/references/expense-research-guide.md").read_text(encoding="utf-8")
    rows = text.split("## 提取要素与矩阵映射", 1)[1].split("## 附件清单与存储结构", 1)[0]
    assert "| expense | EXP-010 | 02 |" in rows and "19_申请项目字段明细" in rows
    assert "| expense | EXP-011 | 02 |" in rows and "18_费用项目字段明细" in rows
    assert "申请项目行字段明细收集表（工作表：19_申请项目字段明细）" in text
    assert "费用项目行字段明细收集表（工作表：18_费用项目字段明细）" in text


def test_expense_source_audit_is_outside_skill_reference():
    audit = ROOT.parents[1] / "docs/research-sources/expense-v2.0.md"
    assert audit.is_file()
    text = audit.read_text(encoding="utf-8")
    assert "费用模块调研更新_2.0.xlsx" in text
    assert "21 个" in text and "64 个" in text
    assert "DY-EXP-07" in text
    guide = (ROOT / "skills/module-research/references/expense-research-guide.md").read_text(encoding="utf-8")
    assert "费用模块调研更新_2.0.xlsx" not in guide
