# 巡检通用流程

以当前范围最新确认的答卷及关联附件为业务依据，利用现有知识定位规则，再通过工具检索当前系统、只读取证，逐项判断是否对齐。不使用静态巡检矩阵；调研问题投影仅供问答，未确认的填写内容不能替代当前确认答卷。

## 简洁流程

1. **读取最新答卷。** dy_module_memory_read取得当前模块、业务范围的最新确认答案、附件字段明细、答案版本及同步答卷路径。唯一范围直接复用，多范围按业务名称选择；不按文件名或修改时间猜最新，不拿旧下载副本覆盖当前答案。用户刚交回的答卷先由module-research整理、确认保存，再读取。无确认答案时报告该项缺少依据，独立项继续。
2. **使用环境连接。** MCP服务从当前环境变量APP_URL、DB_NAME、APP_USER、APP_PASSWORD取得连接，不要求用户重复提供。先调用dy_odoo_environment_info核对配置是否可用；environment使用DY_ODOO_ENVIRONMENT，未设置时使用工具返回的数据库身份，prepare可省略environment自动复用。不要运行env、打印连接配置或回显账号凭据；缺配置或认证失败时说明缺失项或失败类别，不猜环境、不索要密码。公司及业务日期从答卷复用，仅补实际依赖的缺口。
3. **提取对齐要求。** 调用dy_inspection_prepare，原样保留basis及全部expected_checks。逐题把确认答案、附件requirements整理为“业务要求、适用范围、预期系统值或行为”；返回的detail_checks逐项保留text与source_ref，字段可合并查询但不得省略，未核验填待确认或未检查。每项明确核验目标verification_target：configuration为系统配置、business为业务效果、document为制度或材料本身；类型未明确不作通过结论。缺附件、摘要变化或局部答案标待确认。费用25题、总账37题，实际分母取当前模板全部question_id。
4. **检索知识并查系统。** 按业务词调用dy_knowledge_search，必要时dy_knowledge_module_info定位模块，再以命中reference调用dy_knowledge_read；按next_offset续读到相关规则完整。结合领域source-queries将业务要求映射为模块、模型、字段、关联、权限及计算规则。随后用dy_odoo_module_info或dy_odoo_module_list_installed核对安装版本，dy_odoo_model_fields核对真实字段，再dy_odoo_record_search/read取得当前系统记录。知识解释规则，当前系统查询证明现状；知识未命中、读取不完整、源码与部署版本不符时保留限制，不能推断不存在或通过。
5. **比较并报告。** 逐项比较预期与实值，记录差异、真实证据和未验证边界；不能仅核对记录数量或名称。先dy_inspection_validate核对覆盖和依据，再按下方模板回复。用户确认保存后才dy_inspection_report_save并dy_inspection_report_read；巡检请求不授权修配置或写业务数据。

source-queries仅技术查询配方，不定义业务要求。查询按真实公司、期间及关联关系限定，单页最多100条，满页继续offset分页；共享记录、停用记录和空值语义依据知识及实际字段判断。查询报错、权限不足或分页未完成不能当作未配置。禁止任意RPC、SQL、旁路连接和业务写入，即使其他技能存在配置写工具也不调用。业务效果不能由配置存在替代，支付、制证、关账等仅查既有结果或签认材料，不执行操作来验证。

### 记录查询参数

`dy_odoo_record_search` 的筛选条件放在 `args=[domain]`，字段和分页放在 `kwargs`。下面仅演示参数形状，公司、模型和字段须先按当前答卷与元数据核实；`trace_context` 原样复用当前 basis 并补本题关联标识。

```json
{
  "trace_id": "inspection-query-1",
  "model": "dy.fin.account.period",
  "args": [[["company_id", "=", 7]]],
  "kwargs": {"fields": ["id", "company_id"], "limit": 100, "offset": 0}
}
```

顶层只接受 `trace_id/model/method/args/kwargs/trace_context`；`kwargs` 只接受 `fields/limit/offset/context`，context只接受布尔值active_test。不传order，不把domain、fields、limit或offset放在顶层。未知参数会报错，不会自动转换。失败请求不推进offset；成功满页才继续取下一页。接口未提供排序参数，数据变化或分页稳定性无法确认时保留未检查，不凭分页数量宣称查全。工具成功仅证明读取完成，仍须核对实际筛选范围及证据关联。

系统配置项必须取得当前Odoo证据；材料项须独立核验文件并填写observed_at，晚于答案确认且不晚于verified_at。调研附件是预期依据，不能复制改名作为独立核验证据。业务效果另需真实结果材料或签认。在题级reason中按各要求写清“预期→实值→比较结论”，列明公司/期间范围及分页是否查全；检索成功、空结果或结构校验通过都不等于业务对齐。新增模块/模型/字段发现工具可辅助定位，差异候选工具空结果不证明无差异。

当前答案revision、附件摘要、全部requirements与真实证据逐项记录，包括待确认题的全部已确认附件要求及明细；未核验项可无证据。Odoo trace_context匹配basis、connection_fingerprint和question_id/answer_revision，成功tool.end与odoo.read同时存在；连接安全指纹由服务生成，不填写或显示地址、账号及凭据。要求级证据匹配requirement_id，题级汇总可引用本题实际要求ID。跨范围、模块或连接不能放行。答案、附件或连接变化重新prepare。校验器验证覆盖与证据关联，业务语义、公司/期间选择及查询完整性仍须模型逐项复核。

按[校验合同](../docs/inspection-validation.md)保留全部问题及已确认附件要求。对齐对应通过，有确凿差异对应不通过或部分通过；缺业务依据标待确认，未取得系统证据标未检查，不适用须有确认理由和真实依据。答案或附件变化先重读、重新prepare，不沿用旧巡检结论。补充调研由module-research处理，巡检内不展开整轮调研问卷。

## 固定回复

```text
巡检模块：{模块名称}
业务范围：{公司或阶段}
巡检依据：当前最新确认答卷及关联附件
核对结果：{已对齐数量、存在差异数量、待确认或未检查数量}
差异与待补：{业务要求／系统现状／差异或缺口／证据概述；无则填“无”}
下一步：{补充材料或建议处理；无则省略}
报告：{实际已保存报告链接；尚未保存则说明}
```

内部标识、版本哈希、查询参数、模型技术名及连接信息仅用于工具和证据关联，不在用户回复中展开。来源审计不写Skill或业务记忆；内部校验合同仍须完整填写。
