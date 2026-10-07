# 安全策略

## 支持版本

shared-skills 只对 `main` 分支最新提交提供安全修复。

## 报告漏洞

发现安全漏洞请**不要**在公开 issue 提交。请按以下流程私下报告：

1. 不要 fork 仓库复现，避免公开漏洞细节。
2. 发邮件给仓库 owner（地址见 git commit 历史的 `Author Email`），主题加 `[SECURITY] shared-skills`。
3. 邮件正文写清：受影响版本、复现步骤、影响范围、建议修复方向。
4. 收到后我们将在 72 小时内确认收到，并按严重程度评估修复优先级。

## 处理原则

- 修复期间对外保密，直至补丁发布或达成协调披露时间表。
- 修复以 hotfix 分支发布，并在 GitHub Security Advisories 公告。
- 重大漏洞修复后将在 README 徽章中标注补丁版本。

## 不视为安全漏洞

- 用户误用 `--force` 或 root 权限造成的本地数据损失
- 在生产租户环境运行未审阅的远端技能（请通过 `skillsync verify` 与人工 review 防御）
- 软链指向仓外导致的逻辑异常（`apply` 默认拒绝，需 `--force` 才放行）
