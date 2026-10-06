# 实时页面契约

这些接口于 2026-09-21 在当前百度租户页面只读/受控写入验证。每次运行必须从当前页面重新捕获请求元数据，不得复用 `quark_s`、签名或旧请求体。

## 关注列表

```http
GET /api/v2/UI/IndexPage/GetTableList
  ?listViewName=Recruitment.SingleObjectListView.ApplicantAttentionList
  &metaObjName=Recruitment.ApplicantAttention

POST /api/v2/UI/TableList
  ?viewName=Recruitment.SingleObjectListView.ApplicantAttentionList
  &metaObjName=Recruitment.ApplicantAttention
  &rootPage=attendList
  &rootPageType=4
```

响应候选人位于顶层 `biz_data`。至少使用：`ApplicantID`、`AttentionDate`、`IsAttention`、`HasPermission`、`StdIsDeleted`、`JobId`，以及动态关系前缀下以这些后缀结尾的字段：`Name`、`ApplicantLockStatus`、`ApplicationLockStatus`、`ApplyLockedJobIds`、`ApplyLockedJobTime`、`PredictUnlockTime`。动态前缀不得固化。

## 申请与目标

- `GET /mrest/Recruitment/TalentPoolV6/100000/ITalentDetailProvider/GetJoblistOfApplicant?applicantId=...&type=0`：读取候选人现有岗位；响应包含 `Data.JobList` 和 `Data.ServerTime`。
- `POST /mrest/Recruitment/RecruitmentV6/100000/CheckApplicant/CheckApplicantIsInJob?applicantId=...&jobId=...`：目标岗位重复申请检查。
- `GET /mrest/Recruitment/SettingV6/100000/Process/GetProcessByJobId?jobId=...`：实时解析岗位招聘类型、流程、阶段和状态。
- `GET /mrest/Recruitment/RecruitmentV6/100000/ApplyDetail/GetApplyDetailHeaderById`：推荐后按新 `applyId` 回读目标岗位、流程、阶段、状态、权限和锁。

## 推荐

人才详情页验证的写入口：

```http
POST /mrest/Recruitment/RecruitmentV6/100000/ApplicantOperate/RecommendJobFromStoreDb
```

请求结构应来自当前页面推荐表单。测试中 `MediumId=21` 表示人才库推荐；不要自行推导其他媒介值。结果必须以目标岗位重复检查和申请详情回读为准，不能只看提示文案。

已验证阻塞：候选人有推进中的招聘流程时，服务端可能返回“暂时无法推荐候选人到社招职位”；这是正常跳过，不得绕过或自动重试。

## 写入安全门槛

- 承接岗位必须由当前用户明确配置；不能使用 Skill 作者、测试账号或历史会话的真实岗位。
- 每次运行都重新检查岗位唯一匹配、招聘状态、招聘类型、当前账号权限、目标阶段和状态。
- 对全部关注记录分页扫描；写入逐人串行，响应不确定时先回读，不能重发。
- `isSkipApplyLock`、`isSkipApplicantLock` 或其他绕过权限、锁和占用的参数一律禁止。
- 报告只保存人可读且脱敏的结果；不得保存关注列表或详情接口原始响应。
