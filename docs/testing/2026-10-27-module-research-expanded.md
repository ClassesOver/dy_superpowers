# 模块调研 Skill 扩展测试报告

测试日期：2026-10-27。本文件保留修复前基线：当时实现未通过全部新增场景。后续修复与最新验证见[调研 Skill 修复验证](2026-10-27-module-research-repairs.md)；以下失败数字和费用缺口不代表修复后状态。

## 一、费用内置 QA 的实际缺口

当前 `skills/module-research/SKILL.md` 仅有“总账内置 QA”，引用 `references/general-ledger-research-guide.md`；费用仅复用 `expense-inspection/references/inspection-matrix.md`，没有同等的专用调研 QA 与附件模板指引。

费用现有能力：通用多轮调研流程、矩阵调研子项及验收标准、按项目/模块/检查键/scope 保存与回读、修订历史及待办恢复。缺少的模块规约：按配置项组织的汇总问题、提取要素与子项的显式映射、附件清单及字段/必填要求、附件与问题的对应关系。不能把工具支持所有费用检查键等同于这些内容已补齐，也不能从总账模板臆造费用模板。

## 二、范围与结果

本轮新增三个持久 pytest 文件，参数展开共 46 例，没有用 xfail 或 skip 隐藏新增失败：

| 测试文件 | 场景与实际覆盖 | 结果 |
| --- | --- | --- |
| `test_research_module_matrix_expanded.py` | 费用 91 键、总账 81 键；独立核对配置项及矩阵键，逐条真实写入回读；双工作区、模块与范围隔离、分页、版本、停用、巡检状态门禁 | 4 通过 |
| `test_research_stdio_expanded.py` | 两模块真实 MCP stdio；未确认拒写、未知字段拒绝、主题笔记 CAS、Wiki 清单失效、全矩阵未检查报告及 PDF、报告不覆盖、重启恢复、另一工作区同 trace_id 隔离 | 2 通过 |
| `test_research_source_matrix_expanded.py` | XLSX/DOCX/PDF/文本/PPTX/图片，Unicode、隐藏/合并/公式、空白、损坏、不支持格式、附件联动、复用、脱敏、远程不下载 | 18 通过、22 失败 |

最新全量结果：**467 通过、22 失败、4 跳过**，用时 435.11 秒。4 项跳过为显式配置门控的真实 Odoo/调研来源测试，没有伪称真实环境验收通过。355 个 warnings 保留在测试输出中，未作为失败清零。

`bash scripts/validate-plugin.sh`：**41 通过**，Codex CLI 0.164.0；`uv lock --check` 和 `git diff --check` 通过。插件格式与安装校验通过，不代表新增业务测试通过。矩阵与 stdio 两文件最终单独复跑：**6 通过**。

## 三、失败归因与建议顺序

22 个失败案例归为以下 7 类，并非 22 个独立根因。下列问题本轮只复现、留证，未修改生产实现：

| 顺序 | 问题 | 失败数 | 实际表现与定位 |
| --- | --- | --- | --- |
| 1 | 归档文本资产仍保留合成凭据 | 1 | `source_archive.py:151-158` 原样写资产字节。DOCX 的结构化正文已脱敏，但 `assets/000001.xml` 和衍生 Markdown 仍有测试密码。本例只涉及隔离本地归档，不代表发生远程泄漏。 |
| 2 | 子附件阻塞未汇总至主档 | 2 | `research_sources.py:365-377` 未将 child 的全文问题并入主 status/issues；含图片的 DOCX 子档 partial，主档仍 complete，或者问题总数少计。 |
| 3 | Unicode 候选与全文解码不一致 | 5 | `research_sources.py:188-193` 以 UTF-8 replacement 解析候选；UTF-8 BOM 影响 Q 识别，UTF-16/32 产生错误候选。全文层能正确解码，不能据此断言候选层正确。 |
| 4 | 主来源异常没有结构化档案 | 6 | `research_sources.py:331-355` 先执行候选解析；PPTX/PNG、不支持或损坏文件在归档前抛错，只有 traceback，没有可回查的 partial/failed 档案。 |
| 5 | 本地附件错误标为 not_local | 3 | `research_sources.py:282-290,360-364` 将候选不支持/失败等同没有本地文件，本地 PPTX/PNG 或损坏 DOCX 不归档、path 为空。 |
| 6 | XLSX 关键字段缺失仍 complete | 3 | `research_sources.py:94-109` 对缺检查编号、配置项或结论没有候选问题报告；空结论被跳过。 |
| 7 | 脱敏吞掉正常业务文字 | 2 | `trace.py:38-43` 非引号 secret 值匹配未正确处理中文分隔符等边界，将同回答中的业务尾句一起删除；保密与业务内容保留两项不能只满足其一。 |

建议先处理资产保密与主子档状态，再统一解码及异常契约，最后补齐关键字段和脱敏保真；每项使用本次失败测试作回归，不将断言弱化为接受现状。

图片/PPTX 测试并未证明 OCR 或视觉语义识别能力：测试明确要求缺少候选解析器或视觉复核时返回结构化阻塞，而非编造提取成功。模板字段完整、候选覆盖完整、文档技术提取完整、业务确认与验收通过应分别报告。

## 四、两个模型子 agent 的自主三轮 QA

这部分由主 agent 逐轮扮演模拟顾问提供输入，子 agent 自主读取 Skill、选择检查键、判断授权及下一问题；Python 只执行当轮决定的具体工具调用。它不同于预写候选/答案脚本，也不构成真实人类业务验收。

### 费用范围与恢复

三轮共 14 次真实本地工具调用；最后的新 Python 进程 9 项核验通过。第 1 轮只整理、无授权不写；第 2 轮只保存甲组织范围 `expense:2:01`；第 3 轮新增乙独立 scope 并保留甲整条原文和 revision。未授权的“11月”没有进入费用期间、scope 或业务记忆；限额、预算只保存获授权的问题待办。恢复后仅两条组织范围记录。

报告：`/tmp/dy-model-expense-qa-fAv84N/自主费用QA报告.md`。这证明对应流程边界，不证明费用专用 QA/附件模板已经齐全。

### 总账附件、缺字段与恢复

三轮共 14 次真实工具调用、1 次真实 CLI，18 项计数核验通过。第 1 轮只保存获确认的基础应用结论，没有擅自补凭证应用。第 2 轮真实解析公司应用清单，排除模板示例、将预算已启用保留为观察；沿用 scope_id 和最新 revision 只修订附件依据，历史可回读。第 3 轮只保存公司编码 A2 缺失的问题待办，新进程恢复验证结论及 revision 不变、仍仅一条且没有预算确认记录。

CLI 报 `complete/issues=0/source_subitems=0`，但模板必填公司编码 A2/A3 空缺。模型识别并保留业务字段缺口，没有把文档可读取当作模板齐全或完整调研。

报告：`/tmp/dy-attachment-autonomous-mJFAsn/three-round-report.md`；逐格式根因：同目录 `source-matrix-report.md`。临时证据不是永久归档承诺，结论与复现入口已固化在本报告及仓库测试文件。

## 五、复现命令

在仓库根目录执行，使用既有锁文件和项目 pytest：

```bash
uv run --frozen --project plugins/dy_superpowers/mcp-server --extra test pytest -q plugins/dy_superpowers/mcp-server/tests/test_research_module_matrix_expanded.py plugins/dy_superpowers/mcp-server/tests/test_research_stdio_expanded.py
uv run --frozen --project plugins/dy_superpowers/mcp-server --extra test pytest -q plugins/dy_superpowers/mcp-server/tests/test_research_source_matrix_expanded.py --tb=short
uv run --frozen --project plugins/dy_superpowers/mcp-server --extra test pytest -q --disable-warnings -r s plugins/dy_superpowers/mcp-server/tests tests
bash scripts/validate-plugin.sh
uv lock --check --project plugins/dy_superpowers/mcp-server
git diff --check
```

本轮只新增测试与本报告，未修复生产逻辑、未补费用模板、未提交或推送 Git，未访问真实 Odoo/GitLab 或发布 Wiki。测试业务材料均为隔离临时工作区内的虚构数据。
