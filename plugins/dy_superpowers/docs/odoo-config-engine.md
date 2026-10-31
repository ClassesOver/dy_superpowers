# Odoo 配置引擎边界

兼容目标为 HRP Odoo 12。查询工具保持只读；配置创建/修改、配置导入和模块维护默认关闭，不提供任意 RPC、SQL 或业务动作；删除当前无获批模型，全部拒绝。

## 扩展工具

- 发现与诊断：模块可用列表、模型列表、字段搜索、认证/版本/模块读取检查。仅用当前环境连接，不执行 Docker、数据库直连或日志检查。
- 方案分析：需求分析、方案生成、计划校验只返回候选或静态核对结果，不授予变更权限；差异分析读取当前确认答卷，模块提及不直接视为交付要求。
- 核对计划固定列出环境诊断、模块依赖核对及配置只读核对三个阶段。缺候选时读取已安装模块，缺配置预期时发现模型，同时返回missing_information和blockers，implementation_ready保持false。提供user_answers.configuration_checks（model、company_id、domain、expected_values、可选expected_count）后生成字段元数据查询及带comparison的记录查询；公司与预期冲突明确拒绝。此格式适用于具有company_id字段的配置，其他公司字段结构须先核实元数据并单独设计查询。静态valid只表示计划结构合法，不能代替步骤执行、业务确认或实施授权。
- 配置 CSV：模板、预览、执行仅支持现有可创建配置白名单，每批最多100行、1MB；仅新增，不支持通用业务数据导入或更新。预览需提供相同环境及批次键才能生成确认摘要；执行复用逐条配置权限、版本、公司和重复项检查。非整批事务，失败不回滚已写行。
- 模块维护：安装、升级、卸载执行须在受信任 MCP 进程环境设置 `DY_ODOO_MODULE_WRITE_ENABLED=1`，并以 `DY_ODOO_MODULE_ALLOWED` 逗号分隔精确批准模块；`base`、`web` 不允许作为变更目标，已安装依赖可以复用。安装预览会只读递归解析目标账套的模块声明依赖，返回去重后的依赖图、已安装复用项及拓扑安装顺序；缺失依赖纳入待安装清单，而不是直接拒绝。执行前重新读取完整闭包，依赖、状态或版本变化后必须重新确认；待安装模块全部符合白名单才调用 Odoo 原生安装并回读完整清单。循环、不可访问模块、过渡状态、超过100个模块的闭包或单模块达到100条依赖声明时明确停止，不将部分结果当成完整计划。升级或卸载的批次外已安装反向依赖、安装自动影响风险仍拒绝，不隐式扩大范围。
- 配置写入和导入须设置 `DY_ODOO_CONFIG_WRITE_ENABLED=1`。上述写开关仅由宿主进程传入，不从工作区 `.env` 加载。全部变更绑定连接、内容与幂等键；已尝试项仅回查，不自动重试。备份引用不是工具验证过的可恢复证明；回读一致不等于业务验收。
- `dy_odoo_record_execute` 仅允许 `ir.module.module.check_access_rights` 的固定只读权限检查；`dy_odoo_record_delete` 无可删模型，不提供任意方法或删除通道。

添加连接器前，记录目标的准确 Odoo 修订版本、社区版/企业版类型、已安装模块、自定义附加模块（addon）、数据库、部署形态、开放的远程过程调用（RPC）/应用程序接口（API）和服务身份。不得根据其他 Odoo 主版本推断兼容性。

### 模块变更固定确认模板

升级当前仅提供预览，不执行：Odoo 原生升级会先刷新模块清单再展开反向依赖，现有只读预检无法证明刷新后的完整影响范围。即使开关、白名单、确认摘要均满足，也会在创建尝试日志及变更 RPC 前拒绝升级；不通过通用 RPC 或自动刷新绕过这一限制。安装及受限卸载仍按各自合同执行。

模块安装、升级和卸载预览统一返回 `confirmation` 与由服务端生成的 `confirmation_text`，模板版本为 `module-change/v1`，模型应完整展示，不自行删去影响或阻塞字段：

- 操作、环境及连接目标、用户请求模块和本次变更模块。
- 安装依赖图、模块当前状态与版本、待安装顺序及已安装复用项（升级、卸载不提供安装计划）。
- 白名单缺口 `blocked_modules`、执行阻塞原因 `execution_blockers`、宿主写开关状态、备份引用及 `backup_verified=false`。
- 幂等键、精确 `confirmation_digest` 和验收限制。

模板与摘要不等于用户授权。安装摘要绑定完整依赖计划，不要求调用者回传或改写依赖图；保持原始请求参数与幂等键，用户明确确认后使用同一摘要调用 `dry_run=false, confirmed=true`。升级、卸载的预览仍仅生成本地确认摘要，不宣称已完成线上影响核查。已尝试批次先检查日志，不因安装后依赖状态变化重新执行。备份由用户核实，数据回读通过也不表示业务或浏览器验收通过。

当前适配器提供 `search_read`、指定 ID 读取、`fields_get`、已安装模块列表及模块信息读取。连接参数来自 `APP_URL`、`DB_NAME`、`APP_USER`、`APP_PASSWORD` 环境变量，不设置凭据默认值；`APP_URL` 同时是唯一受信任来源。通用搜索限制模型名格式、结果条数和只读方法；记录读取最多 100 条；已安装模块列表每页最多 100 条，需用 offset 翻页取全量；连接和 Odoo Fault 对外返回脱敏错误。调用者仍须按目标公司、业务和期间选择最小必要 domain 与字段，并验证服务账号的公司访问范围；只读不等同于数据最小化，也不能替代 Odoo 侧权限配置。

## Knowledge

打包边界：Knowledge 默认使用 Zoekt 原生索引，保存文件策略允许收录的 HRP 完整源码文本，不是自研语义片段数据库。索引不依赖原始源码目录；已授权排除的文件不在覆盖范围内。不纳入外部工具实现、项目记忆、trace、业务返回数据或凭据；完整来源、文件清单及构建审计仅留仓库本地 docs/research-sources。

`dy_knowledge_search(query, module?, knowledge_type?, limit=20)` 按字面文本检索，最多返回 50 个文件/摘要，每项摘要最多 240 字符，总响应最多 64 KiB。保留 query/results/count；count 为本次返回项数，total_known/total_count/truncated 单独说明总量及截断。module 以完整 addon 目录名过滤，knowledge_type 仅支持 source/summary；模型名、字段名直接放入 query，不支持原有 model 或 field/model/constraint 等语义过滤，传入会明确报错。索引或服务故障不是“无结果”，不会自动退回旧数据库。

技术名保持字面检索。中文业务短语无源码命中时，使用本地 Jieba 分词（启用 HMM 以保留微信、卡包等未登录词）、排除常见问句停用词，最多12个去重关键词先经 Zoekt AND 检索；确认无命中后才放宽为 OR。人工摘要命中不阻止源码补充；仅查摘要时保持摘要范围。包含技术符号但没有中文问句特征的输入仍按字面处理。search_strategy=literal/keywords、search_terms 和 query_relaxed 返回实际查询方式、词项及是否放宽，原 query 及引用不变。字面及关键词检索均最多取200个候选，先降低翻译文件优先级，再按所有返回命中行的关键词覆盖排序，同分时优先 Python/XML/JS，摘要展示覆盖词最多的命中行；这是有限候选重排，不保证全库最优。直接命中的人工摘要保留在前，关键词泛匹配的摘要置于源码之后。单次原生查询仍受2秒上限约束。查询不完整或失败时不继续回退或放宽。关键词候选不等于语义正确，必须读取代码并按实际模型和模块继续定位；truncated及total_known照常说明覆盖限制。

`dy_knowledge_module_info(module)` 返回单独标记的历史业务摘要及 manifest 源码引用，不自动提取模型、依赖或版本。摘要里的 depends/models/workflow/model_notes 均不能冒充当前索引证明的事实；标准模块保留的 odoo_version 也只是概念摘要版本，以 odoo_version_scope=summary 标明。读取 manifest 和相关代码后再判断，并通过 MCP 核对线上版本。

`dy_knowledge_read(reference, offset=0, limit=4000)` 按字符分页，最多 16000 字符，通过 Zoekt 索引回读完整文本；按 next_offset 读到 complete。仅接受搜索返回的、带索引版本的 zoekt: 引用或 summary: 引用，不打开任意文件路径；旧 src: 引用及版本失效引用须重新检索。完整文件不等于完整调用链，结果缺失不证明对象不存在。

程序和预建索引随插件打包，当前仅支持 Linux x86_64（静态 Go、AMD64 v1），安装和运行不要求 Go、不触发编译。开发期通过 `scripts/build-zoekt-index.sh` 调用原生索引器、凭据扫描器并逐文件回读校验；三个工具路径须显式提供，失败不替换已有索引。仅在当前机器验证过，不宣称所有 Linux 发行版均已验收。

同用户、同索引内容及服务协议共享一个 Python 监督进程和一个 Zoekt 子进程，不按工作区分别启动。跨进程锁统一串行请求，全局空闲 300 秒回收，不中断进行中的请求；监督进程死亡时原生子进程随之退出。服务仅监听本机，禁止继承业务凭据和外部遥测配置，查询与源码结果不持久化在共享状态文件中。源码检索保持只读，不授予任何 Odoo 写入能力。

## 配置写入合同

MCP 进程环境由管理员显式设置 `DY_ODOO_CONFIG_WRITE_ENABLED=1` 才可写入；该开关不从工作区 .env 自动加载。APP_URL 是唯一受信环境；确认必须包含精确 URL 与数据库。客户端不执行 sudo，超级用户拒绝写入；Odoo 原生访问控制和记录规则仍生效。服务账号当前公司必须等于目标公司且在其授权公司集合内。

固定白名单由 configuration.py 维护，Knowledge、技能或请求不能扩展：

| 模型 | 操作 | 允许字段 |
| --- | --- | --- |
| dy.expense.personal.tax.calculation.rule | create/update | name、company_id、effective_date、expiration_date、cal_type、resident_flag |
| dy.fin.account.analytic.plan | create/update | name、code、company_id |
| dy.expense.document.type | 仅 update | name、is_active、fund_application_control、exp_req_doc_type_id、approval_control、account_auditor_control、payment_control、is_calculator_tax、enable_reimbursement_detail、is_share_expenses、is_required_attachment |

元数据须与白名单类型及关联模型一致；不允许嵌套关系命令。运行时还核验 Odoo 12.0 与已安装模块版本：dy_expense 为 12.0.1.0，dy_fin_mdm 为 12.0.0.1，其他版本暂停并重新核对源码；版本号相同不证明部署代码相同。company_id 不可跨公司，company_ids 必须恰为目标单公司，不能修改共享记录或扩展公司范围。关联记录也须可读、属于目标公司。缺少必填字段或现有同键候选时拒绝创建，交由模型分析复用/补充确认；不自动补默认值。

### 预览、确认与执行

1. 先通过只读 MCP 取得目标、旧值、write_date、公司与关联事实，形成方案。创建/更新调用使用工具 schema 中的 `environment={url,database}`、model、company_id、values、idempotency_key、confirmed。更新还必须给出 record_id、与 values 同键的 expected_values、expected_write_date。
2. 调用 `dy_odoo_record_create/update` 并设置 `dry_run=true, confirmed=false`：只做本地参数校验，返回 confirmation_digest，不连接、不声称线上校验通过。
3. 展示精确新旧字段、环境/数据库/公司、真实关联 ID、影响、风险、恢复方案与该摘要，获得用户明确确认。保持全部业务参数和幂等键不变，设置 `dry_run=false, confirmed=true` 并传入原 confirmation_digest。任何实质变更都重新预览确认。摘要只是完整性绑定，不是签名或自动授权；用户确认仍由会话负责。
4. 摘要算法公开：剔除 trace_id、confirmed、confirmation_digest、dry_run，trace_context 只纳入下述答卷关联字段；未关联答卷时仍剔除 trace_context，保持独立配置兼容。合并 operation 后以 `sort_keys=true, ensure_ascii=false, separators=(',', ':'), allow_nan=false` 的 UTF-8 JSON 做 SHA-256。调用方使用 dry_run 取得摘要，无需手算。
5. 执行前核对环境、原生权限、模块/字段、公司和更新快照；更新固定传入 Odoo 12 的 `__last_update` 并发上下文。该机制取决于部署代码，不能宣称所有定制均有原子比较交换保证，操作期间应避免人工并发修改；要求可证明原子更新的场景停止，转人工或专门的服务端实现。

### 按答卷清单配置

配置独立运行，只读取答卷，不依赖巡检准备或巡检报告。由答卷发起配置时，先用 `dy_module_memory_read` 读取当前范围的确认答案及附件明细，模型结合清单数据提取配置预期，使用 Knowledge 和实时字段元数据核对，再逐行比较账套已有记录。源码引用或答卷本身不能证明线上配置已完成，模型也不能仅因有候选而发起写入。

调用 `dy_odoo_record_create/update` 预览时，`trace_context` 携带答卷依据，以下六个字段全部参与配置确认摘要，预览的 confirmation 返回同一组身份：module、scope_id、template_revision、question_id、answer_revision、environment。出现任何答卷关联字段就必须提供完整身份；skill 等纯追踪标签及巡检专用标识（如 requirement_id）不参与摘要。服务连接身份（URL、数据库、账号的摘要）由服务端计入所有配置写入的确认摘要，调用方不传入；连接变化后旧摘要失效。不要去掉关联信息来复用旧摘要。

预览及执行开始时核对当前答卷，线上预检结束后、落盘尝试和写入前再核对一次。该题答案须已确认且 research_complete，必需附件须齐全且内容摘要未变化；模板、范围、题目修订和环境须一致。过期、未完成、附件变化或身份不符时拒绝相关项，重新读取、分析并确认；其他独立事项可继续。上述核对不锁定用户编辑附件或远端配置，不能宣称跨本地答卷与 Odoo 的分布式原子事务。不按答卷的配置继续使用原接口；此关联也不验证模型对自由文本的解释是否正确，业务字段预期仍须用户确认。

成功写入返回 answer_context 便于在配置报告中标明答卷依据；幂等重放仍仅返回尝试状态，必须独立回读。自动回读和更新旧值核对采用与 comparison 相同的严格类型规则，False 不等同 0。最后以同环境、公司和记录 ID 完成 Playwright 验收，报告及截图标明答卷身份。数据通过与界面通过分别记录；单题配置完成不代表整份答卷或模块验收通过。

### 幂等与失败语义

幂等键按当前工作区持久化在 `.dy_superpowers/configuration/`，仅保存摘要、执行状态和真实 ID，不存 values/返回行/密码。执行前原子保存 unknown 占位；同键不同内容拒绝，同键相同内容只返回 already_attempted，不重新写入，也不复用旧的回读通过状态。新建前查同公司同唯一定位键，仍不能替代数据库唯一约束或阻止其他进程换键的并发创建；同一配置项须使用稳定幂等键。

超时、连接中断、响应不明或异常：保留 unknown，不能当作未提交，不自动重试，不换键规避。用只读 MCP 按公司和唯一键查询实际结果，不能唯一确认则暂停关联后续。远程写入成功但回读失败时保留 succeeded，数据仍未通过。回读差异返回 mismatch，不自动回滚、不自动覆盖；恢复修改必须单独预览、确认。没有删除、任意方法或事务回滚接口。

响应分开给出 execution_status、data_readback_status、browser_acceptance_status；accepted 始终为 false。MCP 写入自动回读目标 ID、公司与全部目标字段，不能代替业务效果或浏览器证据。技能使用 Playwright 只读核对同环境、同数据库、同公司、同记录并截图；报告及截图在工作区保存。只有数据与浏览器都通过才能标记本批配置验收完成。

本地 trace 只记录操作/模型/字段数量/状态等元数据，不记录 Odoo 返回行、values 或凭据。真实账套写入必须另行明确环境与授权，本仓库离线测试与源码知识不构成此授权。未支持的模型、权限或业务动作转为人工事项，不提供直连旁路。
