# 招聘协作助手

这是一个可安装的 Codex Skill，用于把已授权的百度 iTalent 招聘流程、人才库简历筛选、候选人推荐计划、本地简历证据评分和 InstSci 文献检索组织到同一个入口。

## 已包含

- `SKILL.md`：主路由、证据边界、隐私边界和写入门控；
- `references/italent-audit.md`：岗位、候选人、初筛、面试、面评、Offer 和通过率口径；
- `references/recommendation-gates.md`：iTalent 推荐/阶段转移的真实 ID、dry-run、锁检查、指纹和回读门槛；
- `references/instsci-bridge.md`：InstSci CLI/MCP 与闭源论文可见浏览器证据边界；
- `references/model-and-provenance.md`：附件工程使用的模型、实际运行模型的记录要求和来源分级；
- `references/install-and-use.md`：依赖 Skill、InstSci、如流和首次调用说明；
- `references/source-provenance.md`：用户请求与附件参考材料的分层；
- `scripts/split_recommendation_batch.py`：把明确输入的前 20 个候选人按 8/12 生成 J103039/J103043 推荐计划；
- `scripts/validate_recommendation_plan.py`：验证计划确实是 8/12、20 人且未开启写入。

## 当前安装位置

本机已安装到：

```text
/Users/rongzhengting/.codex/skills/italent-recruitment-collaborator
/Users/rongzhengting/.codex/skills/baidu-italent
```

`screen-italent-resumes`、`ego-browser`、iTalent 审计/台账相关 Skill 已在本机存在并作为依赖使用。

## 20 份简历分组

这次材料没有提供一份可明确识别、来源一致的 20 人清单：附件工程里既有示例简历，也有设计文档导出的 PDF；下载目录和 `归档.zip` 里的文件来源也不同。因此本交付没有擅自混合它们，也没有向北森提交任何候选人。

拿到真实清单后运行：

```bash
python3 scripts/split_recommendation_batch.py \
  --input /任务目录/resumes.json \
  --output-dir /任务目录/recommendation-plan
python3 scripts/validate_recommendation_plan.py \
  /任务目录/recommendation-plan/recommendation-plan.json
```

计划固定为：

| 分组 | 数量 | 岗位 |
| --- | ---: | --- |
| 前八份 | 8 | AI测试开发工程师 `J103039` |
| 后十二份 | 12 | AI测试开发工程师 `J103043` |

脚本只生成计划，不发送 iTalent 写请求。真实推荐必须由 `baidu-italent` 在当前已授权会话中补齐 `applicant_id`、`apply_id`、流程阶段、状态和状态指纹，再执行 dry-run、当前轮授权和逐候选人回读。

## 模型

附件工程里有两层模型信息：`.env.example` 的历史/环境示例是 `deepseek-v4-pro`，但当前代码 `backend/config.py`、`backend/core/model_gateway.py` 和策略/触达服务把有效运行模型固定为 `gpt-5.6-sol`。因此本次交付应以 **`gpt-5.6-sol` 是附件当前代码事实** 来理解，`deepseek-v4-pro` 只作为历史配置示例保留。这个 Skill 不强制绑定模型；实际调用时以运行环境为准，并在报告里记录实际模型和是否使用脱敏文本。

详见 [references/model-and-provenance.md](references/model-and-provenance.md)。

## 附件审查结论

- 采纳：`海外招聘助手.zip` 的简历解析、PII 脱敏、证据评分、候选人档案、人工审批边界和模型网关实现思路；
- 采纳：`baidu-italent.zip` 的 iTalent 开放 API、页面证据、推荐/阶段转移门控和真实 ID 要求；
- 采纳：`screen-italent-resumes.zip` 的标准简历证据筛选方式；
- 采纳：`Rimagination/instsci` 的文献检索、MCP 和可见浏览器下载边界；
- 不采纳为生产事实：附件中的测试数据、演示简历、动态 `quark_s` 链接、Cookie、Token、`.env`、浏览器 Profile 和模型密钥。

附件中没有一份可明确识别的 20 人真实候选人清单；当前只找到 3 份演示简历、1 份扫描测试 PDF 和若干重复副本/文档 PDF。因此本包只提供可验证的 8/12 计划生成器，不会把演示文件冒充成 20 名候选人，也不会在缺少实时 `applicant_id`、`apply_id`、流程阶段、状态和状态指纹时向 iTalent 写入。

## 分发

把本目录复制到目标机器的 `$CODEX_HOME/skills/` 下即可。若使用本交付的干净压缩包，直接执行：

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
unzip -q italent-recruitment-collaborator.zip \
  -d "${CODEX_HOME:-$HOME/.codex}/skills"
```

解压后应存在：

```text
$CODEX_HOME/skills/italent-recruitment-collaborator/SKILL.md
```

推荐同时安装 `baidu-italent`、`screen-italent-resumes`、`ego-browser` 和 iTalent 审计/台账 Skill。InstSci 还需要 Python 3.10+ 的独立环境和可见浏览器/机构授权。
