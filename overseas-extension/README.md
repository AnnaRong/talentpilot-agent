# TalentPilot LinkedIn 采集器

1. 保持 TalentPilot 运行在默认地址 `http://127.0.0.1:8765`，并先在控制台刷新岗位。
2. 解压下载的插件包。
3. Chrome 打开 `chrome://extensions`，启用开发者模式，选择“加载已解压的扩展程序”。
4. 选择解压后的 `TalentPilot-LinkedIn-Collector` 文件夹。
5. 人工打开已获授权处理的 LinkedIn 候选人公开主页，点击插件，选择岗位并保存。

插件不会自动登录、搜索、触达候选人或绕过 CAPTCHA、安全验证和访问限制。它只读取当前
人工打开的 `https://www.linkedin.com/in/*` 页面，将结构化档案发送到本机 TalentPilot。
