# 调研清单导出与指南对应回归

测试日期：2026-10-29。本轮只使用临时工作区，不修改真实项目调研记忆。

## 验证目标

- 总账、费用共用首表“调研问题清单”的 11 列布局；首表以用户指定参考模板核对列宽、字体、行高和分组合并区域。
- 总账 82 个、费用 92 个子项按各自 research guide 映射表的原顺序输出。逐行核对检查顺序、子项编号、配置子项和完整提取指引，并核对同组配置项及汇总问题。
- 总账 GL-5-017 使用指南中的补充 QA，保留两个独立子项；GL-8-026-02 保留独立检查键。
- 附件页根据模块指南和最近完整归档文件动态生成；保留额外页签及已填内容，当前文件清空的数据不从历史轮次恢复。
- 首次导出无答案，附件仅含标注的示例；后续导出带上已确认记忆及完整性状态。回填优先读当前“答案”，否则读当前“已记忆项”，仍须业务确认才能写入记忆。
- 文件按“模块+调研清单+日期+流水.xlsx”命名；并发导出递增流水，不覆盖旧文件。

## 失败复现与修复

首次工作簿回归为 6 通过、2 失败：费用空答项仍返回展示编号，归档附件未回填。新增逐行指南比较后，暴露费用逐点 QA 覆盖汇总 QA 的解析错误，以及总账补充 QA 未导出的问题。分别限定表的读取范围并接入补充 QA 后通过。

新增多行记忆及同名范围测试后，发现多行结论被拆开、同名范围标识互相覆盖，以及同一行多个范围的完整性状态被混用。现按范围记录分别恢复结论及状态；当前工作簿仍只是待确认来源。

样式比较还复现了表头颜色和字体、正文行高偏离参考的问题，随后按参考值修正。总账和费用首表均已生成 PNG 预览并查看。

## 最终执行记录

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 工作簿专项 | `plugins/dy_superpowers/mcp-server/.venv/bin/python -m pytest -q plugins/dy_superpowers/mcp-server/tests/test_research_workbook.py --basetemp /tmp/dy-workbook-preview-lhkr88/tests` | 11 passed，9.33s |
| 真实 MCP stdio 调研闭环 | `uv run --frozen --project plugins/dy_superpowers/mcp-server --extra test pytest -q plugins/dy_superpowers/mcp-server/tests/test_research_stdio_expanded.py -x --tb=short` | 6 passed，69.56s |
| MCP 与仓库全量测试 | `uv run --frozen --project plugins/dy_superpowers/mcp-server --extra test pytest -q plugins/dy_superpowers/mcp-server/tests tests` | 622 passed，4 skipped，355 warnings，433.71s |
| 官方 CLI 插件校验 | `bash scripts/validate-plugin.sh` | Codex CLI 0.165.0；41 passed，13.08s；包含隔离市场安装与 MCP 协议冒烟 |
| Skill 校验 | `python3 /home/junge/.codex/skills/.system/skill-creator/scripts/quick_validate.py plugins/dy_superpowers/skills/module-research` | Skill is valid! |
| 补丁格式 | `git diff --check` | 退出码 0 |

全量测试的 4 个跳过项是需要显式提供真实 Odoo 环境与调研来源的业务巡检测试，未作为业务验收通过；插件安装与协议验证均实际执行。警告来自依赖弃用提示及被解析业务源码的转义序列。

沙箱内的一次旧测试运行发生 stdio 超时；允许 uv 缓存和子进程运行后，真实 stdio 专项及全量测试均通过。最终结果不使用该次超时运行作为成功依据。

本轮移除了中途生成的固定总账附件 XLSX 副本；通用插件不携带该原始参考文件的副本。用户原有调研日志与来源审计文件保留。本轮未提交、推送或更新已安装插件缓存。
