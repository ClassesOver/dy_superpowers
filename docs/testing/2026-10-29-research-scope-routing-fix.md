# 调研开场范围补问与分流修复验证

## 需求与边界

取消公司/院区、业务范围、适用期间三项固定开场补问。先解析已有附件、读取当前模块记忆、计算存储度，再选择 Excel 清单或互动 QA。具体业务题与保存候选结论确实需要的范围信息仍按需核对；不删除业务指南问题、模板字段、保存授权或范围隔离。

修复验证阶段只修改仓库源码与 Skill，不更新正式安装、不提交或推送，不访问真实 Odoo 或飞书，不将测试答案写入正式项目记忆。测试使用临时工作区及虚构数据；不加载生产 `.env`。后续用户另行授权提交推送，版本升为 `0.1.3`，提交前验证记录另列；原始 XML 日志仅在本机保留，不随代码提交上传。

## 实现合同

| 输入状态 | 返回与下一步 |
| --- | --- |
| 无范围、无记录 | `scope_mode=unscoped`，费用 0/92、总账 0/82，`route=excel` |
| 无范围、只有一个已有范围 | `selected`，复用真实范围标识，按该范围存储度分流 |
| 无范围、存在多个范围 | `multiple`，`scope_progress` 分别列进度；顶层 0% 表示通用清单未扣减题目，不表示没有记忆 |
| 显式范围 | 保持范围过滤与歧义拒绝；不跨范围合并 |
| 已填附件、存储度低 | 优先处理附件答案及其待签认缺口，不重复要求空白清单 |
| 存储度达到 70% | 直接互动补问，不导出 Excel |

未选范围的通用模板保留全部题目，范围和答案栏留空。唯一范围模板只导出该范围未回答题。显式指定新 `scope_id` 且暂无显示名称时，Excel 保留真实稳定标识，响应 `scope` 仍为 `null`，不编造名称。导出仍需明确确认且不覆盖文件；进度查询不写业务记忆。损坏记录仍报错，未映射及完整性未知记录继续返回诊断摘要。

## 测试先行记录

1. 新增无范围分流、单范围阈值、多范围隔离、导出确认测试；修正测试数据的范围约束后，旧实现结果为 **7 failed, 9 passed**。失败涉及 `needs_scope`、未复用范围及导出要求范围，直接复现产品缺口。
2. 修改进度工具和导出流程，共享分流逻辑，保留底层单范围计算的严格合同。
3. 显式已有范围标识导出标签测试先失败（F2 为 `None`），修复显示名称复用。
4. 独立审查发现显式新范围标识导出丢绑定；新增测试先失败（F2 为 `None`），再修复为保留 `site-new`。真正未选范围模板保持空白。
5. 最终进度与导出回归 **21 passed**；独立审查再次执行同一文件，也是 **21 passed**，无剩余阻塞。

## 独立模型行为测试

每次使用全新上下文、只读仓库 Skill；代理自行给出下一步选择，不由主代理合成业务回答。此层是情景行为测试，不是飞书端到端测试。

旧版对照共 5 次：`scope_routing_baseline`、`scope_control_2` 至 `scope_control_5`。无附件、无记忆、无范围，仅请求“费用调研”。5 次均先追问范围并暂停分流，其中一次完整重现三项开场问题。

旧版原话摘录：

- “暂不计算完成比例或导出清单……涉及哪些公司、医院或院区？”
- “当前适用范围尚未明确，暂不计算调研存储度，也不生成 Excel。”
- 三项问卷：“本期上线涉及哪些公司或医院、院区？”、“本次费用调研覆盖哪些业务？”、“本次调研结论适用哪个期间？”

新版共 5 次：`scope_green_1` 至 `scope_green_5`；各运行以下 3 个场景，全部一致：

| 场景 | 5 次实际选择 |
| --- | --- |
| 费用 0/92，无附件、无范围 | 不问三项范围；通过可用飞书 QA 请求导出授权，授权前不调用导出 |
| 三范围分别 45/92、45/92、65/92，本轮未选择 | 分别列进度，请求完整通用清单导出授权；不先要求选范围、不采用最高进度、不合并答案 |
| 已填总账附件一条来源回答待签认，0/82 | 展示真实来源候选，补该条必要范围并继续业务确认；不因 0% 重发空白清单、不提前保存 |

新版原话摘录：

- “当前已存储 0/92 项……是否确认生成 Excel 清单？”
- “顶层 0/92 不代表模块没有已答记录……不合并各范围答案，范围栏留空。”
- “附件已提取一条来源回答，但仍待业务签认……这条结论适用于什么范围？”

第 5 次额外检查总账唯一范围 58/82：选择互动补问、不要求选范围、不导出 Excel。这里只验证一次模型行为；确定性阈值另由 pytest 覆盖。

## 真实子进程与工具验证

`test_stdio_scope_optional_routing_and_generic_export` 分别启动费用、总账真实 MCP stdio 服务，覆盖初始化、无范围 0% 分流、拒绝未确认导出、保存虚构单范围记录、自动复用、增加第二范围、独立进度、导出完整通用模板和记忆字节不变。

初次定向测试在沙箱内为 **38 passed, 2 failed**，两项均在 MCP 初始化超时。未修改产品代码来绕过该限制；相同命令获准在沙箱外重跑后为 **40 passed, 2 deselected**，耗时 7.58 秒。两项 deselected 是该测试文件中原有的完整报告流程，不是新加 stdio 场景。之后又增加了新范围标识及异常摘要两项回归，由最终验证覆盖。

最终全量测试与官方插件校验结果见本文件后续验证记录及 `logs/2026-10-29-scope-routing/pytest-all.xml`。官方校验会在临时 `CODEX_HOME` 中用官方 Codex CLI 安装当前源码，不改变正式安装。

## 最终验证记录

| 验证 | 实际结果 |
| --- | --- |
| 全量初测 | **609 passed, 4 skipped, 2 failed**，468.39 秒；两个失败都是 Skill 旧文案逐字断言，不是工具运行失败 |
| 移除过时断言后的指南与进度/导出回归 | **38 passed**，2.94 秒，`pytest-guide-progress.xml` |
| 最终费用、总账真实 MCP 分流子进程复跑 | **2 passed, 2 deselected**，6.32 秒，`pytest-stdio.xml`；包含真实导出文件与范围栏核对 |
| 最终官方插件安装与协议校验 | **41 passed**，15.16 秒；Codex CLI 0.165.0 |
| Skill quick_validate | `Skill is valid!` |
| git diff --check | 退出码 0 |

全量初测的两项失败分别是 `test_research_memory_progress_routes_by_scope_and_fixed_threshold`（固定匹配旧范围隔离措辞）和 `test_progress_must_run_before_scope_questions_and_project_name_is_not_a_gate`（固定要求旧的公司/核算主体开场问题）。这两项静态文案断言已由进度工具实际分流、范围隔离、阈值和独立模型行为测试替代，未为通过测试恢复旧开场补问。全量初测以后没有再修改产品源码或 Skill，只移除了这两个过时测试；相关测试文件已重新验证。

修复阶段收尾时未再次整套执行删除过时断言后的套件，因此不把初次全量结果改写为“全量全部通过”。原始失败报告保留在 `pytest-all.xml`；通过的后续定向报告分别保存，未合成或篡改原始测试结果。用户授权提交后的全量重跑与此分开记录。

4 项跳过均为需显式生产环境/来源授权的真实 Odoo 端测：两项需 `DY_E2E_ODOO_ENV_FILE`，两项需来源目录和环境配置。本轮并未执行真实 Odoo 业务验收。全量的既有警告包括 WeasyPrint/HarfBuzz 提示及被核对的 Odoo 源文件转义写法，本轮未扩大修改。

运行命令：

```bash
PYTHONPATH=plugins/dy_superpowers/mcp-server/src plugins/dy_superpowers/mcp-server/.venv/bin/python -m pytest -q plugins/dy_superpowers/mcp-server/tests tests --tb=short --junitxml=docs/testing/logs/2026-10-29-scope-routing/pytest-all.xml
PYTHONPATH=plugins/dy_superpowers/mcp-server/src plugins/dy_superpowers/mcp-server/.venv/bin/python -m pytest -q plugins/dy_superpowers/mcp-server/tests/test_module_research_guide.py plugins/dy_superpowers/mcp-server/tests/test_research_progress.py --tb=short --junitxml=docs/testing/logs/2026-10-29-scope-routing/pytest-guide-progress.xml
PYTHONPATH=plugins/dy_superpowers/mcp-server/src plugins/dy_superpowers/mcp-server/.venv/bin/python -m pytest -q plugins/dy_superpowers/mcp-server/tests/test_research_stdio_expanded.py -k scope_optional --tb=short --junitxml=docs/testing/logs/2026-10-29-scope-routing/pytest-stdio.xml
bash scripts/validate-plugin.sh
plugins/dy_superpowers/mcp-server/.venv/bin/python /home/junge/.codex/skills/.system/skill-creator/scripts/quick_validate.py plugins/dy_superpowers/skills/module-research
git diff --check
```

## 0.1.3 提交前验证

用户授权提交推送后，对最终源码、Skill 与版本 `0.1.3` 重新执行完整套件：

- MCP 与市场布局全量测试：**609 passed, 4 skipped, 355 warnings**，459.85 秒，退出码 0。本地原始报告为 `logs/2026-10-29-scope-routing/pytest-precommit.xml`，不随代码上传。
- 官方插件隔离安装与 MCP 协议校验：**41 passed**，13.23 秒，Codex CLI 0.165.0，退出码 0。
- Skill quick_validate：`Skill is valid!`；`git diff --check` 退出码 0。

4 项真实 Odoo 测试仍因缺少显式环境/来源授权而跳过，不视为真实业务验收通过。没有更新正式插件安装或修改正式项目记忆。

全量重跑命令：

```bash
PYTHONPATH=plugins/dy_superpowers/mcp-server/src plugins/dy_superpowers/mcp-server/.venv/bin/python -m pytest -q plugins/dy_superpowers/mcp-server/tests tests --tb=short --disable-warnings --junitxml=docs/testing/logs/2026-10-29-scope-routing/pytest-precommit.xml
```
