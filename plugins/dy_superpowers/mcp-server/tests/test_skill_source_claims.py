"""锁定两份巡检查询参考与 HRP 源码的一致性。

源码目录由 DY_HRP_SOURCE_ROOT 指定（默认 /home/junge/pros/dypowers/data）；
目录不存在时跳过，不阻塞无源码环境。
"""
import ast
import os
import re
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).parents[2]
sys.path.insert(0, str(PLUGIN / "mcp-server/src"))

SOURCE_ROOT = Path(os.environ.get("DY_HRP_SOURCE_ROOT", "/home/junge/pros/dypowers/data"))
pytestmark = pytest.mark.skipif(not SOURCE_ROOT.is_dir(), reason="需要 HRP 源码目录 DY_HRP_SOURCE_ROOT")

# 巡检查询参考声明的模型（源码核对确认的事实）
CLAIMED_MODELS = {
    "skills/expense-inspection/references/source-queries.md": [
        "dy.expense.requisition", "dy.expense.payment.requisition", "dy.expense.repayment.requisition",
        "dy.expense.document.type", "dy.expense.document.type.sequence", "dy.expense.item",
        "dy.expense.city.season", "dy.expense.permission.configuration",
        "dy.expense.permission.configuration.line", "dy.expense.policy", "dy.expense.policy.line",
        "dy.fin.expense.pf", "dy.fin.pf.account.move.generation.rule",
        "dy.budget.control.execution.rule", "dy.budget.control.rule.expense.item.line",
        "dy.budget.control.exp.app.line", "dy.hrfm.expense.execute.rule", "dy.fin.account.period",
    ],
    "skills/general-ledger-inspection/references/source-queries.md": [
        "dy.fin.account.structure", "dy.fin.account.structure.line", "dy.fin.account.period",
        "dy.fin.account.period.open.and.done", "dy.fin.account.analytic.plan",
        "dy.fin.cash.flow.mapping", "dy.account.permissions.settings", "account.move",
        "account.move.line", "dy.fin.acc.account.periodicity.voucher",
        "dy.fin.acc.carry.forward.project.definition", "dy.fin.acc.carry.forward.template",
        "dy.fin.acc.carry.forward.template.line", "dy.fin.acc.carry.forward.entry",
        "dy.fin.acc.carry.forward.entry.line", "account.journal",
        "dy.fin.acc.account.move.discrete.number", "account.mapping", "budget.account.move",
        "budget.move.paralleled.wizard", "budget.statement.of.income.and.expenditure",
        "dy.fin.pf.account.class.define", "dy.fin.pf.account.move.generation.rule",
        "dy.fin.pf.influence.factor.setting", "account.account",
    ],
}

# 声明为临时向导的模型必须真是 TransientModel；其余声明模型不得是 Transient
DECLARED_TRANSIENT = {
    "dy.fin.account.period.open.and.done", "budget.move.paralleled.wizard",
    "budget.statement.of.income.and.expenditure",
}


def _source_models():
    definitions = {}
    for manifest in sorted(SOURCE_ROOT.rglob("__manifest__.py")):
        for path in manifest.parent.rglob("*.py"):
            if path.name == "__init__.py":
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                name = inherit = None
                bases = "".join(ast.unparse(base) for base in node.bases)
                for item in node.body:
                    if isinstance(item, ast.Assign):
                        for target in item.targets:
                            if isinstance(target, ast.Name):
                                if target.id == "_name" and isinstance(item.value, ast.Constant):
                                    name = item.value.value
                                if target.id == "_inherit" and isinstance(item.value, ast.Constant):
                                    inherit = item.value.value
                kind = ("transient" if "TransientModel" in bases
                        else "abstract" if "AbstractModel" in bases else "model")
                for key in {name, inherit}:
                    if key:
                        definitions.setdefault(key, set()).add(kind)
    return definitions


def test_every_claimed_model_exists_in_source():
    definitions = _source_models()
    missing = {model for models in CLAIMED_MODELS.values() for model in models} - definitions.keys()
    assert not missing, f"SKILL 引用了源码中不存在的模型: {sorted(missing)}"


def test_declared_wizards_are_transient_and_others_are_persistent():
    definitions = _source_models()
    claimed = {model for models in CLAIMED_MODELS.values() for model in models}
    for model in sorted(claimed):
        kinds = definitions.get(model, set())
        if model in DECLARED_TRANSIENT:
            assert "transient" in kinds, f"{model} 应为 TransientModel 向导"
        else:
            # 仅当源码中只有 Transient 定义（无任何持久 Model 定义）才要求向导标注
            assert "model" in kinds or "transient" not in kinds, (
                f"{model} 是 TransientModel 但 SKILL 未标注为向导；若为向导请更新 SKILL 并加入 DECLARED_TRANSIENT")


def test_query_reference_models_exist_in_source():
    """查询参考中引用的模型和模型字段必须能回链源码定义。"""
    definitions = _source_models()
    for skill, claimed in CLAIMED_MODELS.items():
        text = (PLUGIN / skill).read_text(encoding="utf-8")
        referenced = set(re.findall(r"`([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)`", text))
        # 允许：Odoo 标准模型、已声明清单、以及“模型.字段”形式的字段引用
        unclaimed = {m for m in referenced
                     if m not in claimed and m not in definitions and ".".join(m.split(".")[:-1]) not in definitions}
        assert not unclaimed, f"{skill} 源码对照章节引用了未核对的模型: {sorted(unclaimed)}"
