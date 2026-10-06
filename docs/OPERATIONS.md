# 运行与接入说明

## 数据目录

- `data/live-captures/verified-latest.json`：此前人工授权浏览器采集的已核对快照，
  不是此服务实时获取的数据。保留原采集时间。
- `data/platform/platform.sqlite3`：新任务、共享对象、事件、审批、提醒、审计和密文数据。
- `data/platform/platform.key`：新增密文库的本机密钥。丢失不可解密；数据库备份需单独保护密钥。
- `data/platform/local-access.txt`：初始本机管理员凭据。不能随安装包共享。
- `data/exports`：旧的人工核对 Excel。下载必须与来源哈希匹配。
- `data/recordings`：旧录屏与事件文件，可能包含敏感画面，不打包。

目录内既有明文文件没有被静默加密或删除。生产环境必须完成加密迁移和保留期审查。

## API

所有 `/api/platform` 接口除 `login` 外都需要登录 cookie 或同会话 Bearer token。
Bearer 值是本应用会话令牌，**不是** ugate、北森 cookie 或用户提供的其他 token。

| 方法与路径 | 用途 |
|---|---|
| POST `/api/platform/login` | user_id + secret，本机登录 |
| POST `/api/platform/users` | 管理员创建 HR/lead/auditor，返回一次性账号密钥 |
| POST `/api/platform/datasets/latest` | 导入已核对本地快照 |
| POST `/api/platform/datasets` | 管理员导入授权 ledger/2 JSON |
| GET `/api/platform/datasets` | 已授权数据集清单 |
| POST `/api/platform/jobs` | kind/dataset_id/options/request_key/budget/timeout |
| GET `/api/platform/jobs` | 权限内的状态与结果 |
| POST `/api/platform/jobs/{id}/cancel` | 所有者或管理员取消任务 |
| GET `/api/platform/events` | 跨部门管理员/审计员事件记录 |
| GET `/api/platform/alerts` | 本地锁提醒，不发送企业消息 |
| POST `/api/platform/actions` | kind/dataset_id/payload，发起审批 |
| POST `/api/platform/actions/{id}/decision` | decision/payload_hash/note |
| POST `/api/platform/actions/{id}/execute` | 消费批准；无外部适配器则阻断 |
| POST `/api/platform/privacy/collect` | 登记有同意证据的本地私密记录 |
| POST `/api/platform/privacy/delete` | candidate_id，删除本地同意库对应记录 |
| POST `/api/platform/privacy/purge-expired` | 清理到期本地记录 |

拒绝原因是业务响应，不要将 HTTP 200 的 `status=blocked` 转述为执行成功。
部门授权目前采用“整份数据集必须均在权限范围内”，不悄悄将全公司数据切片给部门。

## 连接条件

### 北森

现有直接登录链接里的签名是临时上下文，不应复制到服务配置。
需要正式授权 API 合同，或实现当前会话的浏览器只读适配器。逐页采集用稳定 ID 校验，
对未完整覆盖的业务范围单独声明。写入需当前租户、精确申请/候选/岗位/阶段与读回路径。

### BOSS

没有内置网页批量抓取或自动打招呼。需要企业官方合作、许可工具和当前额度规则。
先授权只读候选推荐，再验证审批和发送频控。不要把 200 次招呼当作默认可用额度。
毕业届别是触达硬性条件，按当前年份自动滚动：校招仅限次年届，实习生仅限次年届及以后，
社招仅限当年届及以前；届别缺失或无法核实时禁止打招呼。

### 如流

没有凭聊天中的 ugate token 自动写入任何文档。仍需账户绑定、明确目标 document_id、
官方桥接工具以及被授权的写入/读回能力。企业内网 npm 包安装不代表业务接口可用。

### MCP / InstSci

此应用尚未内置 MCP 客户端。需要经审查的 server 配置、工具 schema/白名单、
认证与超时机制、只读最小调用测试。下载论文仍受来源许可和批量下载审批限制。

## 可选模型

只接受管理员配置的 HTTPS 网关和精确允许域名，不使用用户提交的 URL。
当前网关协议是 OpenAI-compatible `/chat/completions`，包含
`max_completion_tokens` 和 JSON 输出格式。供应商需证明兼容；
不兼容时报错，不伪造备用回答。

只有 `analyst` 流程会尝试调用；默认四模块 `audit` 完全不调用。
数据出站需管理员设置 `LLM_EGRESS_ENABLED=1` 并单独执行任务。
模型只能观察匿名岗位编号对应的数字，不能访问简历、面评、文件或外部发送工具。
数字引用校验不保证解释语义正确，仍需负责人核对。

## 录屏

当前未实现验证通过的专用页面录屏，默认仅事件审计。原生屏幕录制曾记录到其他前台
窗口，故默认关闭。不要为了“全流程有视频”而录制不相关窗口或验证码、账号秘密。
后续需在目标浏览器 context 上实现专用录制、时长与文件验证、访问和保留期管理。

## 恢复

只运行一个 uvicorn worker。任务持久化，异常退出后等待该任务的租约过期，再最多重取一次；
输入错误、权限错误、预算错误直接失败。关闭前等待当前纯计算步骤结束。
取消与超时不是硬终止任意远程网络进程，外部动作目前根本没有接入。

测试命令：

```sh
.venv/bin/python -m unittest discover -s tests -v
```

测试中模拟模型协议不代表联网模型验收，合成候选人不代表北森真实写入验收。
