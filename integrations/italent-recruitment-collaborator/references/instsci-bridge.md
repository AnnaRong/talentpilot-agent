# InstSci 协作边界

`instsci` 是独立的 Python CLI/MCP 项目，不把它的依赖或认证状态复制进 iTalent Skill。

## 适用流程

1. 用 `instsci search` 或 MCP 搜索主题/DOI；
2. 用 `instsci fetch` 获取元数据或 Open Access 结果；
3. 批量 DOI 使用 `instsci papers dois.txt --publisher auto --output <dir>`；
4. 需要机构授权的闭源全文时，使用可见 CloakBrowser，并由用户完成 SSO、2FA、CAPTCHA 或机构选择；
5. 报告逐篇列出 `publisher`, `doi`, `route_attempted`, `institution`, `result`, `evidence`, `next_action`。

## 与招聘工作流的连接

可以把岗位 JD、面试能力项或面评主题作为研究查询输入，但文献结果只能作为岗位设计和人工复核参考，不能直接决定候选人录用/淘汰。

## MCP

当 `instsci-mcp` 已注册时，优先使用其结构化工具查询机构策略、出版社目录和 DOI 元数据；最终闭源 PDF 结果仍需可见浏览器证据。不要把 API Key、Inst Token、Cookie、机构账号或完整会话写入日志、Skill、报告或提交物。
