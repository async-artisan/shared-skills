---
name: commit-cn
description: 用中文撰写 Git commit message，并只暂存本次任务产生的改动。当需要提交代码、写提交说明或在共享工作区执行 git commit 时使用。
platforms: claude, codex, workbuddy, trae, trae-cn
---

# 中文提交与最小暂存

## 规则

1. Commit message 一律使用简体中文，首行说清"改了什么"，正文可补充原因和影响范围。
2. 只暂存本任务产生的文件或行：
   - 整文件只有自己的改动 → 直接 `git add <file>`。
   - 同一文件混有他人改动 → 按行暂存（`git hash-object -w` + `git update-index --cacheinfo`），
     不用交互式 `git add -p`。
3. 提交前检查：
   - `git diff --cached --stat` 确认暂存区只含自己的改动。
   - `git log --oneline -3` 确认并行会话没有刚提交过同样的内容。
4. 共享工作区避免 `git stash`（会与并行改动互相干扰）。
5. 本机文件（记忆、本地测试、工具目录）用 `.git/info/exclude` 排除，不改业务 `.gitignore`，更不提交。

## 示例

```
fix(finance): 修复付款单核销列表备注缺失

- 核销列表补充 remark 字段展示
- 回归脚本 local-tests/service/finance_payment_assert.php 通过
```
