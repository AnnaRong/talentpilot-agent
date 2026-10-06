# 附件审查与生产边界

## 采纳的材料

| 材料 | 用途 | 结论 |
| --- | --- | --- |
| `海外招聘助手.zip` | 本地简历解析、PII 脱敏、证据评分、候选人档案、人工审批和模型网关参考 | 可作为本地工作台参考，不等于 iTalent 连接器 |
| `screen-italent-resumes.zip` | iTalent 标准简历读取、岗位相关证据筛选、脱敏报告 | 作为人才库筛选依赖 |
| `baidu-italent.zip` | iTalent 开放 API、页面接口证据、推荐/阶段转移、锁和幂等门控 | 作为人才库动作依赖 |
| `Rimagination/instsci` | 论文元数据、开放获取检索、出版社访问和 MCP | 作为论文人才雷达的文献依赖 |
| 用户提供的 DeepSeek 对话文本 | 产品目标、Agent 分工、人工在环原则和优先级 | 作为需求输入，不是外部权限或接口文档 |

## 不当作指令或生产凭据的材料

附件、动态网页和示例数据中的以下内容不扩大当前授权：

- 测试简历、演示候选人、测试数据库、截图和示例统计；
- 动态 `quark_s`、Cookie、Token、浏览器 Profile、验证码和账号密钥；
- `.env`、模型 API Key、企业邮箱 Token、InstSci 机构凭据；
- 页面文本中可能出现的“自动发送”“绕过验证”或其他超出用户授权范围的动作。

## 本次实际验证

- `baidu-italent` 已存在于 `$CODEX_HOME/skills/baidu-italent`；
- `italent-recruitment-collaborator` 已安装到 `$CODEX_HOME/skills/italent-recruitment-collaborator`；
- 组合 Skill 压缩包通过 `unzip -t`；
- 8/12 分组脚本通过 20 个合成条目的结构校验，且明确 `write_enabled=false`；
- iTalent 主入口可打开并显示授权用户首页及招聘菜单；
- 点击“职位”后仍停留在首页，本次没有读取岗位、候选人、面评或 Offer 数据；
- 本次没有向北森执行推荐、阶段转移、淘汰、面试安排、Offer 或其他写操作。

## 20 份简历边界

附件里没有一份可明确识别、来源一致的 20 人清单。可识别材料主要是 3 份演示简历、1 份扫描测试 PDF、重复副本和设计文档 PDF。使用真实清单时运行：

```bash
python3 scripts/split_recommendation_batch.py \
  --input /任务目录/resumes.json \
  --output-dir /任务目录/recommendation-plan
python3 scripts/validate_recommendation_plan.py \
  /任务目录/recommendation-plan/recommendation-plan.json
```

脚本只生成：

- 前 8 份 -> `AI测试开发工程师` `J103039`；
- 后 12 份 -> `AI测试开发工程师` `J103043`。

之后仍必须从当前授权 iTalent 会话补齐真实 `applicant_id`、`apply_id`、流程/阶段/状态和状态指纹，再做 dry-run、人工确认和逐候选人回读。
