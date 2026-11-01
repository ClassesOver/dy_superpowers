# 模块调研 Skill 维护约束

本文件约束当前目录及子目录的更新。沿用仓库与插件的安全、中文、pytest 和工作区记忆约定。

## 模板与问题文件

- `SKILL.md` 保留通用调研流程及模块入口；模块问题与附件字段只来自当前发布模板，不在 Skill 中重复维护。
- 模板目录 `mcp-server/src/dy_superpowers_mcp/data/research_templates/<模块>.json` 与问题投影 `references/<模块>-research-question.md` 均由仓库根 `scripts/generate-research-templates.py` 从本目录 `references/` 下的工作簿生成，不手工编辑；需要调整问题、附件或字段时修改工作簿后重新生成并一并提交。
- 问题投影只列模板中的调研问题，每个配置组对应一个答案；附件页名称和字段以模板 `attachments` 为准，名称不同须在工作簿中统一，不在投影里另造别名或字段。
- 生成脚本同时写出来源审计 `docs/research-sources/template-qa-*.md`；来源定位、解析统计和映射缺口只放在该审计文档，不进入 Skill 或问题投影。

## 来源与业务边界

- 使用项目锁定的 openpyxl 只读核对 Excel 全部相关页；展开合并单元格用于理解，同时保留原定位。不得只读首屏或以 Markdown 转换成功认定内容完整。
- 手册提供规约，已填写结果提供字段结构、提取映射及来源事实；不能把其中实际公司、人员、账号、数量或项目结论固化到通用 Skill。模板示例不是项目事实。
- 调研结论、适用范围、例外与验收标准分别保留；不得把观察值当已确认决定，也不得把调研回答当现场巡检通过。
- 不向插件包复制原始 Excel，不新增重复的 `research-checklist.md`，不将附件全文写入业务记忆。
- 调研答卷只经 `dy_research_answers_save` 确认后写入，沿用 `scope_id`、`expected_revision` 及附件引用与 SHA-256；不发明未注册字段，不直接修改真实项目记忆。

## 验证

- 修改前用 pytest 暴露缺失的来源映射或结构差异，再做最小修订；模板与提取要素应可逐项核对，不能仅断言标题存在。
- 重新生成模板必须保留原问题、模板字段及稳定键；数量变化须有来源位置并核对记忆、覆盖率和报告的相关测试。
- 结束前运行调研相关 pytest、Skill quick_validate、仓库插件校验与 `git diff --check`；不能将真实环境跳过报告为业务验收通过。
