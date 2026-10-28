# 飞书平台安装 dy_superpowers 的风险修复与实测

日期：2026-10-28。插件版本：0.1.1。Codex CLI：0.165.0。真实安装环境：Linux、Python 3.12.13。两个仓库均保留用户原有未提交修改；未提交、推送或部署生产服务。

## 已复现并修复

1. **ZIP 入口执行权限丢失**：飞书解压只写字节，Unix 可执行入口变成不可执行文件。新增回归先失败（实际权限 664，预期 755）；解压时仅将带 Unix 执行位的普通文件设为 755，不恢复特殊权限。修复后直接执行解压入口成功，ZIP 安装和回滚测试通过。
2. **插件启动落入缓存目录**：插件将 cwd 固定为插件根目录，因此必须独立传入真实业务工作区。飞书两个 Runner、普通聊天任务和档案生成均将 DY_WORKSPACE_ROOT 固定为任务 cwd，覆盖错误的自定义值。档案快照移除插件 cwd，保留已预装 Python 与快照服务源码，避免将记忆写入插件目录。
3. **MCP 环境隔离误伤业务配置**：MCP 原来未显式转发宿主任务提供的 Odoo/Wiki 变量，机器人配置可能看似生效但插件取不到。新增失败测试后扩展为与服务端 .env 白名单一致的明确业务键，不转发模型密钥或飞书应用密钥。任务环境优先于当前工作区 .env；档案仍不继承全局业务凭据。
4. **QA 结束日志失败产生未处理拒绝**：飞书完整测试发现测试替身缺少 logEvent；补齐契约后，进一步注入真实异步日志失败，确认会产生未处理拒绝。结束日志现等待并捕获失败，只给固定告警，不改变问答本身的失败结果，不伪造用户回答。
5. **调研 Skill 与宿主 QA 能力不匹配**：说明原生 QA、exec 的 feishu_qa/ask_feishu_user、宿主异步续接和文字降级。异步未答不能当成业务确认，档案没有 QA handler 时不能虚构该能力。

## 真实安装验证

通过飞书 createPluginService.installArchive 安装完整插件 ZIP，不是 mock 安装：

- 安装目录及业务目录均位于独立 /tmp，不使用正式 CODEX_HOME。
- 安装阶段自动 uv sync --frozen --no-dev --python 3.12，成功导入依赖并生成准备标记；安装结束插件保持全局关闭。
- 普通聊天原始 start.sh 入口、档案预装 Python 入口均经真实 Codex App Server 发现 27 个 MCP 工具。
- 档案快照未复制 .venv，使用安装时准备的 Python，源码属于独立插件快照。
- 两个临时业务工作区分别调用真实 MCP；空范围返回 needs_scope，而不是编造调研进度。
- confirmed=false 的记忆写入被拒，未产生 profile.md；只在测试工作区 A 使用明确测试授权写入标识正文，B 无该文件，插件目录无业务记忆。

最终成功运行：2026-10-28 21:36:33 至 21:36:55（Asia/Shanghai）。临时根目录 /tmp/feishu-dy-real-Io7qCG。

原始事件日志：/tmp/feishu-dy-real-integration-20261028.log，仓库持久副本为 docs/testing/logs/2026-10-28-feishu-plugin-integration.jsonl。可复现实测脚本：/tmp/feishu-dy-real-integration.mjs；通过飞书 server 下 node --import tsx 启动。

测试脚本本身有两次修正：最初错误拼接 JSON 作为 TOML 覆盖参数，后改为平台实际 pluginOverrideArgument；第二次使用旧 SDK 的 isError 属性，后按锁定 MCP 2.x 改为 is_error。两次失败均未作为产品通过，最终完整重跑成功。

## 已完成的回归

- 飞书完整 server 测试：378 passed、0 failed、1 skipped；跳过项为需真实 PostgreSQL 的集成测试。
- 飞书 typecheck 与 build：退出码均为 0。
- 重新构建后的原生 QA 测试：6 passed、0 skipped，覆盖源码/产物 exec、源码/产物 app-server、后端切换及问答不重置总期限。
- QA 卡片、快速回答、权限、取消、超时、日志故障：37 passed。
- ZIP 与原生插件安装回归：17 passed。
- 插件校验入口 bash scripts/validate-plugin.sh：41 passed。
- 插件最终全量：563 passed、4 skipped、355 warnings，453.67 秒，退出码 0；跳过项需要显式真实 Odoo/来源环境，未冒充线上验收。告警主要来自外部源码与文档渲染依赖。
- 原始总账 xlsx 只读重解析：81 条候选、36 个待确认位置、0 条自动签认。没有修改原 Excel 或正式业务记忆。
- 两仓 git diff --check 通过。

完整日志：/tmp/feishu-full-regression-20261028.log、/tmp/feishu-qa-built-final-20261028.log、/tmp/dy-plugin-validation-20261028.log、/tmp/dy-full-regression-final-20261028.log。临时文件可能由系统清理，本报告保留关键事实。

## 验收边界与上线条件

本次是真实 CLI、真实 ZIP 安装、真实依赖安装和真实 MCP 子进程验证。QA 原生集成测试使用本机确定性模型协议响应和测试飞书客户端，不是实际模型决定何时提问，也不是飞书线上用户点卡片。未发送生产飞书消息、未发布 Wiki、未修改 Odoo、未采纳合成业务结论。

还需运行环境提供 uv、可用包源、Python 3.12 及 WeasyPrint 系统库；飞书镜像已有对应准备逻辑。本机通过不等于所有 CPU/操作系统都能安装锁文件；不支持的平台应明确安装失败。档案任务有意不继承全局连接凭据，不应为了“可用”而取消隔离。

上线需重新构建并部署飞书 server，再通过管理端上传本次插件包或更新仓库版本，确认安装成功后绑定机器人/档案。源码改好不等于正在运行的服务或远程仓库自动更新。当前没有执行生产部署，不作无条件“100% 可用”承诺。
