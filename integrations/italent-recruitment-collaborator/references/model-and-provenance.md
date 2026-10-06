# 模型与来源记录

## 本地附件中的模型配置

`海外招聘助手.zip` 同时保留了历史配置示例和当前运行时约束，不能把两者混写成一个模型结论：

- `.env.example` 中的历史/环境示例是 `LLM_BASE_URL=https://oneapi-comate.baidu-int.com/v1`、`LLM_MODEL=deepseek-v4-pro`；
- 当前 `backend/config.py` 将有效运行模型固定为 `gpt-5.6-sol`，忽略外部传入的 `LLM_MODEL` 值；评分和通用 Chat 路径读取 `settings.LLM_MODEL`；
- `backend/core/model_gateway.py` 的 Responses 路径直接使用 `gpt-5.6-sol`，策略 Agent、Outreach Agent 和邮件草稿 Agent 也固定使用该模型；
- `backend/config.py` 和模型服务会读取 One API 兼容网关的 API Key，但 Skill 包不携带 Key；
- 本地简历评分在模型调用前做 PII 脱敏，模型不应收到姓名、邮箱、电话等明文联系方式。

因此，本次 Skill 的准确表述是：**附件当前代码事实使用 `gpt-5.6-sol`，`deepseek-v4-pro` 仅是附件中的历史/环境示例，不是当前代码的有效模型绑定。** 这仍不是本 Skill 对所有环境的强制配置。安装后如果运行环境使用其他模型，应在报告中记录实际 provider、base URL 的非敏感主机名、模型名、调用时间和是否使用脱敏输入。

## 本 Skill 的模型边界

- 岗位 ID、候选人 ID、阶段、面试时间、结果和 Offer 状态必须来自页面/API证据；
- 模型只能做文本整理、字段抽取、岗位相关证据归纳和草稿生成；
- 模型不能凭空补齐缺失字段、猜测岗位流程 ID、决定生产写入或代替人工录用判断；
- 模型输出必须经过结构校验、证据引用检查和隐私清理。

## 来源分级

每个结论标记来源：

1. `live_italent_ui`：当前可见 iTalent 页面；
2. `live_italent_api`：当前页面生成并验证的接口响应；
3. `local_attachment`：用户提供的本地工程/文档；
4. `user_instruction`：用户明确指定的岗位、数量或分组；
5. `model_summary`：模型对已有事实的摘要，不新增事实；
6. `unavailable`：页面未展示、未采集或无法核验。

## 当前版本说明

报告不应写“系统已永久接通”或“自动每天更新”，除非当前运行确实验证了连接、任务调度和回读。一次浏览器会话成功只代表本次授权运行成功。
