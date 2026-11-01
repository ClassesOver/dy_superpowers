# Odoo 只读查询规则

只通过插件 MCP 的 `dy_odoo_*` 只读工具核对当前账套。不得通过脚本、浏览器、直连 RPC、SQL 或数据库旁路读取或修改 Odoo。

## 环境连接

- MCP 服务从当前环境变量 `APP_URL`、`DB_NAME`、`APP_USER`、`APP_PASSWORD` 取得连接，不要求用户重复提供。
- 先调用 `dy_odoo_environment_info` 核对配置是否可用；环境标识使用 `DY_ODOO_ENVIRONMENT`，未设置时使用工具返回的数据库身份。
- 不运行 `env`、不打印连接配置、不回显账号或凭据；缺配置或认证失败时说明缺失项或失败类别，不猜环境、不索要密码。
- 公司、业务日期等范围信息优先复用已有确认内容，仅补实际依赖的缺口；缺失只阻塞依赖它的分支。

## 核对顺序

1. `dy_odoo_module_info` 或 `dy_odoo_module_list_installed` 核对模块安装状态与版本。
2. `dy_odoo_model_fields` 核对真实字段、类型与关联。
3. `dy_odoo_record_search` / `dy_odoo_record_read` 取得当前记录。

## 记录查询参数

`dy_odoo_record_search` 的筛选条件放在 `args=[domain]`，字段和分页放在 `kwargs`。以下仅演示参数形状，公司、模型和字段须先按当前需求与字段元数据核实：

```json
{
  "trace_id": "query-1",
  "model": "dy.fin.account.period",
  "args": [[["company_id", "=", 7]]],
  "kwargs": {"fields": ["id", "company_id"], "limit": 100, "offset": 0}
}
```

- 顶层只接受 `trace_id/model/method/args/kwargs/trace_context`；`kwargs` 只接受 `fields/limit/offset/context`，`context` 只接受布尔值 `active_test`。不传 `order`，不把 domain、fields、limit 或 offset 放在顶层；未知参数会报错，不会自动转换。
- 单页最多 100 条。成功满页才继续 `offset` 取下一页；失败请求不推进 `offset`。接口不提供排序参数，数据变化或分页稳定性无法确认时如实说明，不凭分页数量宣称查全。
- 查询按真实公司、期间及关联关系限定，读取最小字段。共享记录、停用记录和空值语义依据知识及实际字段判断，不照搬其他模型。

## 结果边界

- 查询报错、权限不足或分页未完成，不能当作“未配置”或“不存在”。
- 工具成功只证明读取完成，仍须核对实际筛选范围及证据关联；空结果或记录存在都不等于业务对齐。
- Odoo 返回行不写入项目记忆或 trace 正文。
