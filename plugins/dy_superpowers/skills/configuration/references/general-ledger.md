# 总账配置定位

按需求选取入口，关联事实不自动形成配置顺序。

| 需求叫法 | 首轮检索词/技术线索 | 实时核对重点 |
| --- | --- | --- |
| 科目、结构、辅助核算 | dy_fin_mdm、dy.fin.account.structure、dy.fin.account.analytic.plan | 公司、必填字段、结构关联、已有记录唯一性 |
| 期间、期间模板 | dy.fin.account.period、dy.fin.account.period.template | 业务日期、公司、期间状态；临时向导不是状态事实源 |
| 预算科目映射 | account.mapping、dy_fin_gaap | 财务与预算科目关系及实际安装模块 |
| 凭证生成规则 | dy_fin_pf、dy.fin.pf.account.move.generation.rule | 条件与影响因素、源码约束、账套已有规则 |

不得把过账、开关账、结转、反过账等业务动作当成配置 create/update；权限或字段不支持时转为明确的人工事项。模型出现于 Knowledge 不代表允许写入。
