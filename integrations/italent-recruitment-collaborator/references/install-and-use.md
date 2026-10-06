# 安装与使用

## Skill 安装

把本目录复制到 `$CODEX_HOME/skills/italent-recruitment-collaborator`（未设置时是 `~/.codex/skills/italent-recruitment-collaborator`）。

使用本交付的压缩包时，压缩包顶层已经是 `italent-recruitment-collaborator/`，可以直接解压：

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
unzip -q italent-recruitment-collaborator.zip \
  -d "${CODEX_HOME:-$HOME/.codex}/skills"
test -f "${CODEX_HOME:-$HOME/.codex}/skills/italent-recruitment-collaborator/SKILL.md"
```

推荐同时安装依赖 Skill：

```bash
python3 /path/to/skill-installer/scripts/install-skill-from-github.py \
  --repo citrolabs/ego-lite --path skills/ego-browser --ref main
```

`baidu-italent.zip` 和 `screen-italent-resumes.zip` 是外部打包 Skill。安装时先解压，确认每个目录根部存在 `SKILL.md`，再分别放到：

```text
$CODEX_HOME/skills/baidu-italent/
$CODEX_HOME/skills/screen-italent-resumes/
```

不要把 `.env`、候选人数据库、简历、浏览器 Profile、Cookie 或 Token 放进 Skill 目录。

## InstSci

InstSci 要求 Python >= 3.10。推荐在独立环境安装：

```bash
pipx install git+https://github.com/Rimagination/instsci.git
# 或
uv tool install git+https://github.com/Rimagination/instsci.git
```

需要 MCP 时运行 `instsci-mcp`，并按目标客户端注册。MCP 注册和机构登录状态不等于已经取得某篇闭源论文的下载授权。

## 如流

如需使用用户指定的百度如流安装器，由用户在已授权网络和账户环境中执行：

```bash
npm_config_registry=http://registry.npm.baidu-int.com/ \
  npx -y @baidu/infoflow-installer@next
```

如流发布属于独立写入动作：先完成本地 Excel 和校验，再确认目标文档和写入内容，发布后回读核验。没有目标文档或编辑权限时只交付本地文件。

## 首次调用

- 审计：`使用 $italent-recruitment-collaborator 只读统计当前 iTalent 招聘流程，并生成岗位/候选人/面评/Offer 报告。`
- 筛选：`使用 $italent-recruitment-collaborator 按岗位证据筛选 iTalent 人才库，先给我脱敏候选人清单，不要写入。`
- 分批计划：`使用 $italent-recruitment-collaborator 将这份 20 人清单前 8 人分到 J103039，后 12 人分到 J103043，先生成 dry-run 计划。`
- 文献：`使用 $italent-recruitment-collaborator 通过 InstSci 检索与 AI 测试开发面试能力相关的论文，并区分 Open Access 和机构授权结果。`
