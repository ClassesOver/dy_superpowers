# HRP 源码索引

数据来源：当前可读的鼎医 HRP Odoo 12 源码，覆盖 16 个仓库目录、166 个 addon manifest（技术名、中文名、版本、依赖均提取自 `__manifest__.py`）。依赖仅表示 manifest 的源码声明，不代表目标账套已安装，也不证明线上工作流。
用途：巡检时把巡检矩阵的配置项名称（业务叫法）映射到实际技术模块和模型；实际安装状态、字段与定制仍须用 `dy_odoo_module_list_installed` / `dy_odoo_model_fields` / `dy_odoo_record_search` 在目标账套核验。源码索引不是账套事实。

## dy-finance（总账（财务会计），8 个 addon）

| 技术名 | 名称 | 版本 | 依赖 |
| --- | --- | --- | --- |
| `account_accountant` | 会计核算 | 1.1 | account,web_tour |
| `dy_fin_acc` | 总账-凭证管理 | 0.1 | dy_fin_mdm |
| `dy_fin_acc_hr` | 财务转账-薪酬 | 0.1 | dy_fin_acc,dy_salary |
| `dy_fin_gaap` | 总账-预算会计 | 1.0 | dy_fin_acc |
| `dy_fin_mdm` | 总账主数据 | 0.1 | dy_base,account_accountant,dy_base_data |
| `dy_fin_rm` | 合并报表 | 0.1 | dy_base_data,dy_fin_acc |
| `dy_medical_income` | 医疗收入&财务会计集成 | 3.0 | dy_fin_mdm,dy_base_generic_mapping,dy_fin_pf |
| `dy_medical_income_budget` | 医疗收入&预算会计集成 | 3.0 | dy_fin_mdm,dy_medical_income |

## dy-acc-center（会计平台中心，15 个 addon）

| 技术名 | 名称 | 版本 | 依赖 |
| --- | --- | --- | --- |
| `dy_fin_ap_pf` | 应付管理财务会计平台 | 1.0 | dy_fin_ap,dy_fin_pf |
| `dy_fin_asset` | 资产管理财务会计平台 | 1.0 | dy_asset,dy_fin_pf |
| `dy_fin_asset_fund` | 财务会计平台&资产经费来源集成 | 1.0 | dy_fin_asset,dy_asset_funds_source |
| `dy_fin_budget_ap_pf` | 应付管理预算会计平台 | 1.0 | dy_fin_ap_pf,dy_fin_budget_pf |
| `dy_fin_budget_expense_pf` | 费用管理预算会计平台 | 1.0 | dy_fin_expense_pf,dy_fin_budget_pf |
| `dy_fin_budget_income_pf` | 医疗收入预算会计平台 | 1.0 | dy_fin_income_pf,dy_fin_budget_pf |
| `dy_fin_budget_pf` | 预算会计平台-基础 | 1.0 | dy_fin_pf,dy_fin_gaap |
| `dy_fin_contract_pf` | 合同财务会计平台 | 1.0 | dy_fin_pf,dy_contracts |
| `dy_fin_expense_pf` | 费用财务会计平台 | 1.0 | dy_expense,dy_fin_pf |
| `dy_fin_general_pf` | 通用会计平台 | 1.0 | dy_fin_pf |
| `dy_fin_hrfm_pf` | 科研财务会计平台 | 1.0 | dy_hrfm,dy_fin_pf |
| `dy_fin_income_pf` | 医疗收入财务会计平台 | 1.0 | dy_medical_income,dy_fin_pf |
| `dy_fin_pf` | 财务会计平台-基础 | 1.0 | dy_fin_acc |
| `dy_fin_salary_pf` | 薪酬财务会计平台 | 1.0 | dy_salary,dy_fin_pf |
| `dy_fin_stock` | 库存财务会计平台 | 1.0 | dy_stock,dy_fin_pf |

## dy-expense（费用管理，1 个 addon）

| 技术名 | 名称 | 版本 | 依赖 |
| --- | --- | --- | --- |
| `dy_expense` | 费用管理 | 1.0 | dy_fin_mdm |

## dy-budget（预算管理，11 个 addon）

| 技术名 | 名称 | 版本 | 依赖 |
| --- | --- | --- | --- |
| `dy_budget_business` | 预算管理 |  | dy_base,dy_budget_data |
| `dy_budget_control` | 预算控制 |  | product,dy_budget_business |
| `dy_budget_control_account` | 预算控制&财务会计集成 |  | dy_budget_control,dy_fin_acc |
| `dy_budget_control_ap` | 预算控制&应付管理集成 | 1.0 | dy_fin_ap,dy_budget_control |
| `dy_budget_control_expense` | 预算控制&费用报销集成 |  | dy_budget_control,dy_expense |
| `dy_budget_control_purchase` | 预算控制&采购管理集成 |  | dy_budget_control,dy_purchase |
| `dy_budget_control_stock` | 预算控制&库存管理集成 |  | dy_budget_control,dy_stock |
| `dy_budget_data` | 预算主数据 |  | dy_base,dy_base_data |
| `dy_budget_external` | 预算外围系统集成 |  | dy_budget_control |
| `dy_budget_project_library` | 项目库 |  | dy_budget_data |
| `dy_budget_project_library_business` | 项目库-预算编制 |  | dy_budget_project_library,dy_budget_business |

## dy-hrfm（科研经费，2 个 addon）

| 技术名 | 名称 | 版本 | 依赖 |
| --- | --- | --- | --- |
| `dy_hrfm` | 科研管理 | 2.0 | dy_base,dy_base_data,dy_fin_mdm |
| `dy_hrfm_expense` | 科研管理&费用管理集成 | 2.0 | dy_hrfm,dy_expense |

## dy-ap（应付管理，8 个 addon）

| 技术名 | 名称 | 版本 | 依赖 |
| --- | --- | --- | --- |
| `dy_ap_budget_contracts` | 应付管理&预算管理&合同管理集成 | 1.0 | dy_fin_ap_contracts,dy_contracts_budget,dy_budget_control_ap |
| `dy_ap_contracts_base` | 应付管理-合同基础模块 | 1.0 | dy_fin_ap |
| `dy_ap_hrfm_contracts` | 通用合同&科研模块-应付付款 | 1.0 | dy_contracts_hrfm,dy_fin_ap_contracts,dy_budget_control_ap |
| `dy_ap_scm` | 应付管理&库存管理集成 | 1.0 | dy_fin_ap,dy_stock |
| `dy_ap_scm_contract` | 应付管理&采购合同集成 | 1.0 | dy_ap_scm,dy_purchase_contract,dy_ap_contracts_base |
| `dy_ap_srm_interface` | 应付srm接口 | 1.0 | dy_ap_scm,dy_scm_interface |
| `dy_fin_ap` | 应付管理 | 1.0 | payment,account,dy_base_data,dy_fin_mdm |
| `dy_fin_ap_contracts` | 合同应付集成 | 1.0 | dy_ap_contracts_base,dy_contracts |

## dy-hr（人力资源，13 个 addon）

| 技术名 | 名称 | 版本 | 依赖 |
| --- | --- | --- | --- |
| `dy_appraisal` | 鼎医绩效评估模块 | 1.0 | dy_hr |
| `dy_attendance_manage` | 考勤管理 | 1.0 | dy_salary,dy_interface_platform |
| `dy_employee_appointment_manage` | 员工聘任管理 | 1.0 | dy_hr,dy_base |
| `dy_employee_calendar_middle` | 考勤&培训集成 | 1.0 | dy_attendance_manage,dy_website_slides |
| `dy_hr` | 人事管理 | 1.0 | dy_base_data,dy_base_hpage,hr_contract |
| `dy_hr_doc_application` | 个人证明申请管理 | 1.0 | dy_hr,mail,web,base |
| `dy_hr_fin` | 人事管理&财务主数据集成 | 1.0 | dy_hr,dy_fin_mdm |
| `dy_pre_entry_manage` | 预入职管理 | 1.0 | dy_hr,mobile_wechat_gzh |
| `dy_recruitment` | 鼎医招聘模块 | 1.0 | dy_hr,hr_recruitment_survey |
| `dy_salary` | 薪酬管理 | 1.0 | dy_hr,dy_base_hpage |
| `dy_website_slides` | 线上培训 | 2.2 | portal_rating,website,website_profile,dy_hr |
| `portal_rating` | Portal Rating | 1.0 | portal,rating |
| `website_profile` | Website profile | 1.0 | website_partner,gamification |

## dy-base（鼎医基础，34 个 addon）

| 技术名 | 名称 | 版本 | 依赖 |
| --- | --- | --- | --- |
| `auditlog` | 审计日志 | 12.0.1.0.0 | base |
| `auto_backup` | 数据库备份 | 12.0.0.0 | base,dy_base |
| `base_user_role` | 用户角色 | 12.0.1.1.0 | base,dy_base |
| `dy_auth_lock` | Login Lock | 1.0 | dy_base |
| `dy_base` | 鼎医基础 |  | web,base,hr,mail,auth_signup,websocket,decimal_precision |
| `dy_base_active` | 模块激活码 | 1.0.0 | base |
| `dy_base_data` | 鼎医基础数据 |  | dy_base,base_address_city,dy_base_hpage,payment |
| `dy_base_hpage` | 首页管理 | 1.0.0 | dy_base,base_user_role |
| `dy_base_irmodel_overview_export` | dy_base_irmodel_overview_export |  | dy_base |
| `dy_base_login` | 登陆页面 | 1.0.0 | dy_base,auth_oauth |
| `dy_copyright` | 鼎医备案 | 2.0 | base,web |
| `dy_external_message` | 外部消息服务 | 1.0 | sms_aliyun,sms_qcloud,sms_wechat |
| `dy_log` | 请求记录日志 |  | base,dy_base |
| `dy_menu_base` | 侧边栏菜单&多页签基础 | 1.0.0 | dy_base |
| `dy_multi_tab_plugin` | 多页签 | 1.0.0 | dy_menu_base |
| `dy_onlyoffice` | ONLYOFFICE | 2.1.0 | base,mail |
| `dy_password_protection` | 密保修改密码 | 1.0.0 | dy_base |
| `dy_question_feedback` | 问题反馈单 | 2.0 | dy_base_data |
| `dy_requirements_change_request` | 需求与变更申请 | 2.0 | base,hr,mail,dy_base,dy_base_data,dy_base_hpage |
| `dy_sidebar_menu` | 侧边栏菜单 | 1.0.0 | dy_menu_base |
| `dy_system_collect` | 系统数据收集 | 1.0 | dy_base |
| `dy_user_verification` | 用户登录控制 | 2.0 | dy_base |
| `muk_session_store` | redis/pg会话存储 | 12.0.2.1.0 | muk_utils |
| `muk_utils` | redis/pg会话存储基础 | 12.0.2.0.7 | base_setup |
| `online_helper` | 在线帮助 | 1.0 | base,web |
| `password_security` | 密码强度配置 | 12.0.1.1.1 | auth_signup,auth_password_policy_signup |
| `single_sign` | 单点登陆 | 1.0 | base_user_role,auth_oauth |
| `sms_aliyun` | 阿里云短信 | 2.0 | sms_base |
| `sms_base` | SMS短信应用 | 2.0 | base,auth_oauth |
| `sms_enterprise_wechat` | 企业微信通知 | 2.0 | dy_base,sms_base,mobile_integrate_wechat |
| `sms_qcloud` | 腾讯云短信 | 2.0 | sms_base |
| `sms_wechat` | 微信公众号集成 | 1.0 | dy_base |
| `store_encrypt` | store_encrypt | 0.1 | base |
| `websocket` | 实时消息推送 | 2.0 | web,bus |

以上为原有 8 组源码；其余 8 组列于下文，完整清单共 166 个 addon。

## 巡检重点模块与关键模型对照

| 技术名 | 关键模型（巡检查询入口） |
| --- | --- |
| `dy_expense` | dy.expense.requisition / dy.expense.report / dy.expense.report.payment / dy.expense.payment.requisition / dy.expense.repayment.requisition / dy.expense.document.type / dy.expense.item / dy.expense.city.season / dy.expense.permission.configuration / dy.expense.policy / dy.expense.policy.line |
| `dy_fin_mdm` | dy.fin.account.structure / dy.fin.account.period（持久期间状态）/ dy.fin.account.period.open.and.done（临时操作向导，不作状态证据）/ dy.fin.account.analytic.plan / dy.fin.cash.flow.mapping / dy.account.permissions.settings |
| `dy_fin_acc` | account.move（扩展）/ dy.fin.acc.account.periodicity.voucher / account.move.generate / dy.fin.acc.account.move.summary |
| `dy_fin_gaap` | account.mapping / budget.account.move / budget.statement.of.income.and.expenditure / budget.move.paralleled.wizard |
| `dy_fin_pf` | dy.fin.pf.account.class.define / dy.fin.pf.account.move.generation.rule / dy.fin.pf.condition.source.mapping / dy.fin.pf.influence.factor.setting |
| `dy_fin_expense_pf` | dy.fin.expense.pf |
| `dy_fin_budget_pf` | （无新模型，扩展预算凭证通道） |
| `dy_fin_budget_expense_pf` | （无新模型，扩展费用预算记账） |
| `dy_budget_control_expense` | dy.budget.control.rule.expense.item.line / dy.budget.control.exp.app.line |
| `dy_hrfm` | dy.hrfm.funds.application / dy.hrfm.budget.project / dy.hrfm.funds.account / dy.hrfm.expense.execute.rule |
| `dy_hrfm_expense` | 科研管理与费用管理集成；依赖 dy_hrfm、dy_expense，不能只凭基础科研模块已安装判集成可用 |
| `dy_budget_control` | 预算控制核心（预算方案、控制规则、占用/扣减记录） |
| `dy_fin_ap` | 应付管理单据与付款 |

## 使用约定

- 巡检矩阵中的业务叫法（如“费用管理”“会计平台-费用”“预算会计平台-基础”）对应本表技术名（`dy_expense`、`dy_fin_expense_pf`、`dy_fin_budget_pf`）；检查模块安装时用技术名查 `ir.module.module`。
- 模块列表每页最多 100 条；返回满页必须继续用 offset 翻页，取全量后才能下“未安装”结论。
- 查配置记录前先用 `dy_odoo_model_fields` 核对该账套字段是否与源码一致；定制 addon 可能增删字段。
- 源码版本与账套实际版本可能不同：`ir.module.module.latest_version` 是账套事实，本表版本是源码仓库事实。
- 当前源码范围包含 OCR 集成 `ocr_expense`、银医集成 `dy_bank_expense`、移动费用 `mobile_expense` 和采购管理 `dy_purchase`；清单中的依赖关系只用于定位源码，不作为线上安装或工作流证据。

## dy-bank-payment（银医与银行接口，11 个 addon）

| 技术名 | 名称 | 版本 | manifest 依赖 |
| --- | --- | --- | --- |
| `dy_abc_interface` | 农行银医接口 | 1.0 | dy_bank_base |
| `dy_bank_ap` | 银医平台&应付管理集成 |  | dy_bank_base,dy_fin_ap |
| `dy_bank_base` | 银医基础平台 |  | dy_base,dy_fin_mdm |
| `dy_bank_contracts` | 银医平台-合同 |  | dy_bank_base,dy_contracts_payment |
| `dy_bank_expense` | 银医平台&费用报销集成 |  | dy_bank_base,dy_expense |
| `dy_bank_salary` | 银医平台&薪酬集成 |  | dy_bank_base,dy_salary |
| `dy_bc_interface` | 中行银医接口 | 1.0.0 | dy_bank_base |
| `dy_cbc_interface` | 交行银医接口 | 1.0.0 | dy_bank_base |
| `dy_ccb_interface` | 建行银医接口 | 1.0.0 | dy_bank_base |
| `dy_cmb_interface` | 招行银医接口 | 1.0.0 | dy_bank_base |
| `dy_icbc_interface` | 工行银医接口 | 1.0.0 | dy_bank_base |

## dy-contracts（合同与招采，8 个 addon）

| 技术名 | 名称 | 版本 | manifest 依赖 |
| --- | --- | --- | --- |
| `dy_contracts` | 通用合同 | 1.0 | dy_base,payment |
| `dy_contracts_budget` | 通用合同&预算管理集成 | 1.0 | dy_contracts,dy_budget_control |
| `dy_contracts_budget_payment` | 通用合同付款行政预算 | 1.0 | dy_contracts_payment,dy_contracts_budget |
| `dy_contracts_hrfm` | 通用合同&科研模块 | 1.0 | dy_contracts_budget,dy_hrfm |
| `dy_contracts_hrfm_payment` | 通用合同&科研模块-合同付款 | 1.0 | dy_contracts_payment,dy_contracts_hrfm |
| `dy_contracts_payment` | 合同付款 | 1.0 | dy_contracts |
| `dy_contracts_receive` | 合同收款 | 1.0 | dy_contracts |
| `dy_procurement` | 招采管理 | 12.0.1.21 | dy_base,dy_budget_business,dy_contracts |

## dy-cst（项目成本，5 个 addon）

| 技术名 | 名称 | 版本 | manifest 依赖 |
| --- | --- | --- | --- |
| `dy_abc` | 项目成本 | 0.1 | dy_base,dy_base_data,dy_base_hpage,dy_hmd |
| `dy_abc_asset` | 项目成本&资产管理集成 | 0.1 | dy_abc,dy_asset,dy_asset_funds_source |
| `dy_abc_hdc` | 项目成本&科室成本集成 | 0.1 | dy_abc,dy_hdc |
| `dy_abc_mul` | 项目成本-多院区 | 1.0 | dy_abc,dy_hmd |
| `dy_hmd` | 成本主数据 | 0.1 | dy_base,dy_base_data,uom,dy_base_generic_mapping,decimal_precision |

## dy-drg（病案成本，4 个 addon）

| 技术名 | 名称 | 版本 | manifest 依赖 |
| --- | --- | --- | --- |
| `dy_drg` | 病案成本 | 0.1 | dy_base,dy_base_data,dy_hmd |
| `dy_drg_abc` | 病案成本&项目成本集成 | 0.1 | dy_drg,dy_abc |
| `dy_drg_hdc` | 病案成本&科室成本集成 | 0.1 | dy_drg,dy_hdc |
| `dy_drg_mul` | 病案成本-多院区 | 0.1 | dy_drg |

## dy-equipment（设备管理，5 个 addon）

| 技术名 | 名称 | 版本 | manifest 依赖 |
| --- | --- | --- | --- |
| `dy_eq_asset` | 设备管理&资产管理集成 | 2.0 | dy_equipment,dy_asset |
| `dy_eq_scm` | 设备采购 | 2.0 | dy_equipment,dy_sea |
| `dy_eq_scm_asset` | 设备管理&资产管理采购集成 | 2.0 | dy_eq_scm,dy_eq_asset,dy_asset_scm |
| `dy_equipment` | 设备管理 | 2.0 | dy_base |
| `dy_sea` | 设备&资产供应链属性调整 | 2.0 | dy_stock,dy_scm_mdm |

## dy-hdc（科室成本，3 个 addon）

| 技术名 | 名称 | 版本 | manifest 依赖 |
| --- | --- | --- | --- |
| `dy_hdc` | 科室成本 | 0.1 | dy_base,dy_hmd |
| `dy_hdc_acc` | 科室成本&财务会计集成 | 0.1 | dy_hdc,dy_fin_acc,dy_fin_mdm |
| `dy_hdc_mul` | 科室成本-多院区模块 | 0.1 | dy_hdc |

## dy-mobile-service（移动与 OCR，28 个 addon）

| 技术名 | 名称 | 版本 | manifest 依赖 |
| --- | --- | --- | --- |
| `dy_mobile_base` | 移动端基础 | 1.0.0 | hr,dy_base_data |
| `dy_ocr_client` | 鼎医OCR接口客户端 | 1.0.0 | ocr_base |
| `dy_sys_demonstration_client` | 售前演示环境预约平台客户端 | 1.0 | dy_base |
| `dy_sys_demonstration_server` | 售前演示环境预约平台服务端 | 1.0 | dy_base,dy_base_data,dy_mobile_base,mobile_integrate_wechat |
| `mobile_app_approval` | 移动端审批流 | 1.0.0 | dy_mobile_base |
| `mobile_app_asset` | 资产 移动管理 | 1.0.0 | dy_asset_inventory |
| `mobile_app_attendance` | 考勤 移动管理 | 1.0.0 | dy_mobile_base,dy_attendance_manage |
| `mobile_app_equipment` | 设备 移动管理 | 1.0.0 | dy_mobile_base,dy_equipment |
| `mobile_app_fill` | 移动端动态填报 | 1.0.0 | dy_mobile_base |
| `mobile_app_hr` | Hr 移动管理 | 1.0.0 | dy_mobile_base,dy_hr |
| `mobile_app_salary` | 薪酬 移动管理 | 1.0.0 | dy_mobile_base,dy_salary,mobile_app_hr |
| `mobile_app_stock` | 库存 移动管理 | 1.0.0 | dy_scm_interface |
| `mobile_budget` | 移动端预算个性化 | 1.0.0 | dy_mobile_base,dy_budget_business |
| `mobile_expense` | 移动端费用个性化 | 1.0.0 | dy_mobile_base,mobile_app_fill,mobile_app_approval,dy_expense |
| `mobile_integrate_dingtalk` | 企业钉钉集成 | 1.0.0 | dy_mobile_base,mobile_integrate_wechat |
| `mobile_integrate_feishu` | 飞书集成 | 1.0.0 | dy_mobile_base,mobile_integrate_wechat |
| `mobile_integrate_hippius` | 移动端海马汇集成 | 1.0.0 | dy_mobile_base |
| `mobile_integrate_wechat` | 企业微信集成 | 1.0.0 | dy_mobile_base |
| `mobile_integrate_xunfei` | 讯飞接口集成 | 1.0.0 | dy_mobile_base |
| `mobile_problem_management_platform` | 问题管理平台集成 | 1.0.0 | dy_mobile_base |
| `mobile_question_feedback` | 移动问题反馈单 | 1.0.0 | dy_mobile_base,dy_question_feedback |
| `mobile_wechat_gzh` | 微信公众号集成 | 1.0.0 | dy_mobile_base |
| `ocr_base` | OCR发票基础 |  | dy_mobile_base |
| `ocr_contracts` | OCR&合同管理集成 | 1.0.0 | ocr_base,mobile_app_approval,mobile_app_fill,dy_contracts_payment |
| `ocr_expense` | OCR&费用管理集成 |  | ocr_base,dy_mobile_base,mobile_app_approval,dy_expense,mobile_app_fill,mobile_expense |
| `ocr_invoice_demo` | 发票识别示例 |  | ocr_base,dy_ap_scm |
| `ocr_wechat_import` | 微信卡包发票导入供ocr使用 |  | ocr_base,mobile_integrate_wechat |
| `ticket_rolling_ocr` | 卷票机 | 1.0.0 | dy_base |

## dy-scm（供应链与采购，10 个 addon）

| 技术名 | 名称 | 版本 | manifest 依赖 |
| --- | --- | --- | --- |
| `dy_product_expiry` | 产品效期 |  | product_expiry,dy_scm_mdm |
| `dy_purchase` | 采购管理 | 1.0 | dy_scm_mdm |
| `dy_purchase_contract` | 采购合同 | 1.0 | dy_purchase,dy_contracts,dy_stock |
| `dy_purchase_contract_budget` | 采购合同&预算管理集成 | 1.0 | dy_purchase_contract,dy_contracts_budget |
| `dy_scm_interface` | 供应链接口集成 | 1.0 | dy_scm_mdm,dy_purchase,dy_stock |
| `dy_scm_mdm` | 供应链主数据 | 1.0 | stock,purchase,account,dy_base,dy_base_data |
| `dy_stock` | 库存管理 | 1.0 | dy_scm_mdm,dy_purchase |
| `dy_stock_account` | 库存管理&财务主数据集成 | 1.0 | stock_account |
| `dy_stock_cost_adjustment` | 鼎医成本调整模块 | 1.0 | dy_stock |
| `dy_stock_cost_adjustment_ap` | 库存管理&应付管理成本调整集成 | 1.0 | dy_stock_cost_adjustment,dy_ap_scm |

当前清单共 166 个 addon，分布于 dy-acc-center、dy-ap、dy-bank-payment、dy-base、dy-budget、dy-contracts、dy-cst、dy-drg、dy-equipment、dy-expense、dy-finance、dy-hdc、dy-hr、dy-hrfm、dy-mobile-service、dy-scm 这 16 个源码仓库目录。
