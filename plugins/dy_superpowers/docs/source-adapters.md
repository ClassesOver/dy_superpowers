# 来源适配器契约

每个适配器声明来源类型、负责事实类别的权威性、读写模式、目标标识、凭据来源、刷新方式和支持字段。结果携带来源引用、读取时间、版本标识以及新鲜度和可用状态。

首批运行时适配器包含 Odoo 12 JSON-RPC 只读适配器，通过 `/jsonrpc` 调用 `common.login` 和受限的 `object.execute_kw`。它支持 `search_read`、指定记录读取、字段元数据读取和模块状态读取，连接参数从 `APP_URL`、`DB_NAME`、`APP_USER`、`APP_PASSWORD` 环境变量读取，`APP_URL` 同时限定受信任 origin，默认最多返回 100 条记录。不可用的来源应作为覆盖缺口明确报告；不得用记忆填补当前事实。任意方法调用和 SQL 不在适配器范围内；受开关控制的配置写入与模块安装/卸载复用同一连接，但由各自工具的白名单、确认摘要与回读合同约束，见 [配置引擎边界](odoo-config-engine.md)。

Odoo HTTP/HTTPS 认证及只读调用的请求超时为 20 秒，不修改全局 socket 默认值。标准 Odoo 12 模块依赖字段使用 `dependencies_id`，返回依赖记录 ID；需要名称时通过 `ir.module.module.dependency` 的只读查询读取，不能把 manifest 的 `depends` 当作模型字段。
