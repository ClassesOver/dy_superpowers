# 面向巡检的记忆读取优化实施计划

**目标：** 保留 Markdown 事实源和确认边界，减少历史加载，提供同范围巡检准备与版本复查视图。

**已批准设计：** 本轮会话中用户确认的完整性门槛、精简/批量读取、巡检准备与复查视图。不更换数据库，不迁移正式记忆，不提交推送或替换正式安装。

**技术：** Python、pytest、既有 stdio MCP。按当前会话逐项执行，保留上一轮未提交修复。

## 1. 统一完整性门槛
- [x] 在 test_inspection_validation.py 增加缺失 research_complete 的回归：明确巡检结果必须拒绝，待确认记录仍可表达缺口。
- [x] 先观察失败，再将 inspection.py 的缺省完整改为显式 True；旧夹具明确提供完整性，不修改生产旧记录。
- [x] 验证调研与报告校验、旧版本失效和只更换版本号不可延续通过。

## 2. 按需历史、批量键与精简进度
- [x] 在新 test_memory_inspection_views.py 用临时真实记录验证分页后才展开历史、include_history=False 不展开、checks 批量过滤及范围不混用。
- [x] research.py 增加 include_history、checks 可选参数，保持原读取默认兼容；MCP 提供 current/summary/full 视图，历史显式读取。
- [x] research_progress.py 的异常项只返回键、范围、版本、状态、完整性摘要，不重复返回结论与历史。
- [x] 验证分页、损坏记录拒绝、旧调用兼容和只读不改文件。

## 3. 巡检准备与复查视图
- [x] 新建 inspection_prepare.py 和 test_inspection_prepare.py，注册 dy_inspection_prepare。输入 module、scope_id、environment、可选 checks 与 previous_records；所有标识必须显式提供。
- [x] 按当前矩阵关联当前调研，返回 research_ready / missing / retired / incomplete / completeness_unknown，以及验收标准、补问入口、当前依赖版本；不重复存储结论。入口来自指南，实际一题最小缺口仍须结合事实拆分，不新增自动问答逻辑。
- [x] 上次记录按完整键对齐，调研/矩阵/范围/环境/时点变化只标需复查，不自动延续通过，不改旧报告；不匹配的历史记录显式列出。
- [x] 分开呈现调研存储度、矩阵调研准备覆盖和上次结果数量；无上次记录不虚构验证结果。
- [x] 隔离工具链验证：局部补问保存后仅对应键变为 research_ready，其他阻塞不消失；版本修订仅影响对应依赖。真实 stdio 验证工具发现和读取，不将虚构夹具等同生产业务端到端验收。

## 4. 文档与完成校验
- [x] 更新 memory.md、inspection-validation.md 的工具用法与限制，保留来源档案/确认事实/巡检证据边界。
- [x] 运行定向 pytest、全量 MCP/市场 pytest、官方 validate-plugin.sh 和 git diff --check；真实业务与确定性夹具分开报告。全量595通过；审查后排除已通过的22项后端测试复跑576通过；官方校验41通过，4项真实Odoo测试跳过。
- [x] 保存本地验证日志，核对正式业务记忆未变。139个非锁文件在收尾窗口的文件清单及SHA-256一致；详见 docs/testing/2026-10-28-inspection-memory-optimization.md。
