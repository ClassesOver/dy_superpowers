# 确认后局部保存与范围补问修复验证

## 问题与修复边界

截图场景：业务方确认移动报销需要、OCR 识别需要、微信导入不需要，首次卡片明确仅确认、不保存；随后明确要求保存记忆，模型仍要求公司/院区、业务范围、期间三题全部补齐后才能写入。

根因是费用指南同时规定“每个确认批次必须带公司/院区、业务范围、期间”和“scope 至少明确三项范围”。此前修复只取消分流前的固定问题，未取消保存环节门槛。工具本来允许按非空范围说明保存局部事实，且 `research_complete=false` 不计入完整存储度，不能支撑明确巡检结果。

本次仅修改调研 Skill、费用指南及真实 MCP 回归测试。业务确认与保存授权齐备后，先保存实际确认的局部事实并回读，不再为了保存自动开启范围 QA。未知范围如实标待补，必要时用真实追踪标识及确认批次隔离；后续补范围沿用原 `scope_id` 和最新版本，保留历史。没有业务确认或没有保存授权不写入；“保存了吗”只查询；不写入未确认内容、不自动发布 Wiki、不把局部事实视为巡检通过。

截图中的三项原答保存在两个已有键：移动报销需求归入 `EXP-001-01` 的局部应用需求；OCR 与微信导入分别保留在 `EXP-001-02`。不推断基础应用或移动端已经安装、启用，不编造新的检查键。

## 独立模型行为对照

评估代理均为全新上下文，完整读取仓库主 Skill 与费用指南，仅做只读行为选择；没有实际写业务记忆、没有替用户生成业务答案。代理可见相同场景事实，未提供期望答案。此层不是飞书端到端联调。

旧版 `save_scope_baseline_1` 至 `save_scope_baseline_5` 共 5 次，全部选择先发范围 QA、暂不保存。原话摘录：

- 1：“目前还不能落盘：费用调研记忆要求明确公司/院区、业务范围和期间。”
- 2：“范围回答返回前，不调用 dy_research_record_write。”
- 3：“不能立即保存……不能补足缺失的适用范围。”
- 4：“业务结论已确认……但必填适用范围仍缺失。”
- 5：“下一步先只读，再发飞书 QA，不直接写记忆。”

新版 `save_scope_green_1` 至 `save_scope_green_5` 共 5 次，全部选择保存两条局部记录、回读、不发范围 QA。原话摘录：

- 1：“下一步直接保存已确认的局部事实，不再确认、不调用飞书 QA。”
- 2：“本轮只保存，不导出清单、不发飞书 QA、不重复确认。”
- 3：“立即保存已确认局部事实并回读，不再发确认卡、不补问范围。”
- 4：“下一步直接执行局部保存，不再调用飞书 QA。”
- 5：“本轮应直接保存已确认局部事实……不先追问范围或暂缓的 OCR 票种。”

5 次新版均保留以下边界：仅点“不保存”卡片不写入；局部记录传 `research_complete=false`；未知范围使用真实批次隔离；成功回读后才报保存；巡检仍为 `still_pending`。其中 2、3、5 的计划示意把 `check_order` 写为数字，实际 MCP 接口要求文本；本测试只验证行为分支，不能据此宣称所有示意参数可直接调用。真实参数协议由下述 MCP 子进程测试覆盖。

另由全新上下文代理 `partial_save_boundaries` 做一次五场景边界评估，并核对实际 MCP schema：

| 场景 | 实际决策 |
| --- | --- |
| 仅确认不保存后问“保存了吗” | 只读取和回答状态，不写、不拉范围 QA |
| 点击“确认并保存已明确需求”，范围未知 | 直接局部写入并回读，暂缺范围隔离，不先补问 |
| 来源回答待签认，用户只说保存 | 保存授权不替代业务确认，先最小批次业务确认 |
| 新会话相反结论、范围未知，旧记录只有一个范围 | 创建本次独立批次标识，不用进度自动选中的旧 `scope_id` 覆盖原记录 |
| 明确原批次属于甲院报销2026年且授权补齐 | 读原标识和版本后修订范围；其余要素缺失仍为局部记录 |

该补充评估每场景仅一次，不作为稳定性统计或正式环境验收。

## 真实工具与回归

新增参数化测试 `test_stdio_partial_save_with_unknown_scope_resumes_without_completing_research` 分别启动费用与总账 MCP 真实 stdio 服务，并跨两次服务会话验证：

1. 缺少明确确认的写入被拒绝，不产生模块记忆。
2. 范围待补的已确认局部事实写入、按键回读，完整存储度仍为 0。
3. 尝试用该局部记录支撑巡检“通过”被完整性门禁拒绝。
4. 新会话可读取原记录；另一未知范围批次获得不同 `scope_id`。
5. 原批次补齐范围沿用原标识及最新版本，历史中保留旧范围，其他批次不合并。

初次沙箱内运行在 MCP 初始化超时，不作为产品缺陷或有效业务失败。获准沙箱外运行后修正两处测试客户端问题：使用已安装 SDK 的序列化字段读取错误状态；多范围回读使用模块列表，单键不指定范围应拒绝歧义。未修改产品 runtime 来迁就测试。最终新增子进程测试 **2 passed, 4 deselected**，7.33 秒；4 项 deselected 为同文件其他既有场景，后续完整套件覆盖。

指南、进度与巡检门禁定向回归 **64 passed**，3.42 秒。第一次为 63 passed/1 failed，失败是既有静态断言匹配批次说明原话；保持原有准确表述后通过，未削弱业务确认或范围隔离行为。Skill quick_validate 返回 `Skill is valid!`，`git diff --check` 退出码 0。

官方插件隔离安装与 MCP 协议校验 **41 passed**，13.54 秒；Codex CLI 0.165.0。独立只读审查未发现 Critical/Important 问题，并独立复跑新增子进程测试得到 **2 passed, 4 deselected**，7.40 秒；其沙箱内初始化超时与主代理相同，未进入业务断言。

完整 MCP 与市场回归 **611 passed, 4 skipped, 355 warnings**，470.93 秒，退出码 0。4 项均为需显式环境/来源授权的真实 Odoo 测试，未执行真实业务验收；既有依赖与来源文件警告未在本轮扩展修复。原始 XML 路径：`/tmp/dy-partial-save-6zcYtp/pytest-all.xml`，仅保存在本机。

验证命令：

```bash
PYTHONPATH=plugins/dy_superpowers/mcp-server/src plugins/dy_superpowers/mcp-server/.venv/bin/python -m pytest -q plugins/dy_superpowers/mcp-server/tests/test_research_stdio_expanded.py -k partial_save --tb=short
PYTHONPATH=plugins/dy_superpowers/mcp-server/src plugins/dy_superpowers/mcp-server/.venv/bin/python -m pytest -q plugins/dy_superpowers/mcp-server/tests/test_module_research_guide.py plugins/dy_superpowers/mcp-server/tests/test_research_progress.py plugins/dy_superpowers/mcp-server/tests/test_inspection_validation.py --tb=short
PYTHONPATH=plugins/dy_superpowers/mcp-server/src plugins/dy_superpowers/mcp-server/.venv/bin/python -m pytest -q plugins/dy_superpowers/mcp-server/tests tests --tb=short --disable-warnings --junitxml=/tmp/dy-partial-save-6zcYtp/pytest-all.xml
bash scripts/validate-plugin.sh
plugins/dy_superpowers/mcp-server/.venv/bin/python /home/junge/.codex/skills/.system/skill-creator/scripts/quick_validate.py plugins/dy_superpowers/skills/module-research
git diff --check
```

## 环境边界

测试不加载生产 `.env`，不访问真实飞书或 Odoo，不保存截图业务内容到正式项目记忆。只读模型决策与真实 MCP 协议测试不能替代部署后的飞书联调。本次未更新正式插件缓存、未部署或重启机器人。
