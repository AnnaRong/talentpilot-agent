# iTalent 推荐与写入门控

## 路由

推荐候选人到一个岗位和已有申请阶段转移是两类动作：

- `recommend_to_position`：候选人只有人才库上下文，需要真实 `applicant_id` 和 `apply_id`，以及目标 `job_id`；
- `transfer_existing_process`：候选人已经在岗位流程中，需要同一流程的真实 `apply_id`、`process_id`、`phase_id` 和 `status_id`。

不能用候选人姓名、岗位名称或界面标签代替系统 ID，不能猜测阶段/状态。

## dry-run 结果

写入前必须输出：

```text
candidate system IDs
current position/state
target position/state
recruitment type
permission/lock/duplicate checks
candidate count
batch size
expiry
confirmation fingerprint
immediate read-back path
```

默认每批不超过 20 人，即使平台文档允许更大批次。每批重新检查当前状态并生成指纹；不确定响应不自动重试，先回读。

## 当前轮授权

生产写入同时需要：

1. 用户在当前轮明确指定候选人集合、目标岗位和目标阶段/状态；
2. 当前页面/API 已解析出真实 `job_id`、`phase_id`、`status_id`；
3. 申请关系、权限、锁、重复推荐和招聘类型检查通过；
4. 确认指纹仍匹配且未过期；
5. 已知认证发送通道和立即回读路径；
6. 按候选人记录成功、锁冲突、已在岗位、失败或不确定结果。

不能通过 `isSkipApplyLock`、`isSkipApplicantLock` 或类似参数绕过锁和归属检查。

## 20 份简历分配

本次指定的两个岗位是：

| 分组 | 数量 | 岗位名 | 岗位 ID |
| --- | ---: | --- | --- |
| 前八份 | 8 | AI测试开发工程师 | J103039 |
| 后十二份 | 12 | AI测试开发工程师 | J103043 |

脚本只生成候选人分组和推荐计划。计划中的真实申请 ID、流程阶段、状态和指纹必须在当前 iTalent 会话中补齐。
