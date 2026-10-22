# 来源适配器契约

每个适配器声明来源类型、负责事实类别的权威性、读写模式、目标标识、凭据来源、刷新方式和支持字段。结果携带来源引用、读取时间、版本标识以及新鲜度和可用状态。

首批运行时适配器包含 Odoo 12 JSON-RPC 只读适配器，通过 `/jsonrpc` 调用 `common.login` 和受限的 `object.execute_kw`。它支持 `search_read`、指定记录读取、字段元数据读取和模块状态读取，连接参数从 `ODOO_URL`、`ODOO_DB`、`ODOO_USERNAME`、`ODOO_PASSWORD` 环境变量读取，目标必须另行加入 `DY_ODOO_ALLOWED_ORIGINS` 精确允许列表，默认最多返回 100 条记录。不可用的来源应作为覆盖缺口明确报告；不得用记忆填补当前事实。任意方法调用、写入、SQL 和模块管理不在适配器范围内。

Odoo HTTP/HTTPS 认证及只读调用的请求超时为 20 秒，不修改全局 socket 默认值。标准 Odoo 12 模块依赖字段使用 `dependencies_id`，返回依赖记录 ID；需要名称时通过 `ir.module.module.dependency` 的只读查询读取，不能把 manifest 的 `depends` 当作模型字段。
