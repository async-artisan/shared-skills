# 贡献指南

感谢你对 skill-hub 的兴趣！这是一个跨平台 Agent 技能中心枢纽，所有贡献都欢迎，但请先读完本文。

## 仓库边界

事实源（入库、跨机同步）：

- `skills/<slug>/SKILL.md` — canonical 技能
- `registry/catalog.yaml` — 技能台账
- `tools/skillsync/` — CLI 与 Web 控制台源码
- 仓骨架（`bin/`、`platform/`、`manifests/`、`.claude-plugin/` 等）

机器本地数据（**不入库**，由 `.gitignore` 兜底）：

- `registry/state.yaml`、`registry/audit.logl`、`registry/undo/`、`registry/translate-cache.json`
- `*.bak-*`（apply/resolve 产生的回滚副本）

如果你的 PR 误把上述路径加入索引，CI 会失败。

## 开发约定

- **零第三方依赖**：仅使用 Python 3 标准库。新增功能不要引入 `pip install` 才能跑的包。
- **分层**：`core.py`（路径/常量/哈希）→ `store.py`（catalog/state 读写 + audit）→ `undo.py`（Tx 逆操作收集器）→ 业务模块（`doctor`/`adopt`/`apply`/`resolve`/`gitsync`/`webapp`）→ `cli.py`（命令注册）→ `web/index.html`（单文件 UI）。
- **稳定性红线**：DOM id、`window.*` 函数名、API 路径一经发布不得变更，只能新增。
- **写操作三件套**：UI `confirm` 二次确认 + 服务端 `Tx` 记录逆操作 + `audit()` 留痕，缺一不可。
- **金额/哈希裁决**：用 `bcmath` 或 `hashlib`，不用二进制浮点。
- **中文提交**：`git commit -m "feat(skillsync): …"` 或 `fix(web): …`，正文说明 why 而非 what。

## 开发流程

1. Fork 仓库，从 `main` 切出特性分支：`git checkout -b feat/your-feature`。
2. 改代码前先读相关源码；同一文件多处编辑串行执行，写完立即复核落盘片段。
3. 新增/改动逻辑必须有自动化测试。业务单元测试放在 `tests/unit/`（使用
   `unittest`）；包级测试可放在 `tools/skillsync/tests/`（使用 `pytest`）。所有测试
   必须使用临时 `SKILLSYNC_HOME` 和临时平台目录，不得读写真实用户技能目录：
   ```bash
   python3 -m pip install pytest
   python3 -m unittest discover -s tests/unit -v
   PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=tools/skillsync \
     python3 -m pytest tools/skillsync/tests -v
   python3 tests/cli_smoke.py
   ```
4. 跑一遍隔离 smoke（包含 `doctor --strict`、`verify`、`apply` dry-run、`--help`、
   `--version`），确保本机无 regression。直接操作真实平台目录前需要单独确认环境。
5. 涉及 UI 的改动，浏览器实测一遍（`bin/skillsync serve --no-browser` 后访问 `http://127.0.0.1:48920/`）。
6. 提交时只暂存本任务产生的文件，不要 `git add -A`（避免误纳 `registry/audit.logl` 等机器本地数据）。
7. PR 标题简短（≤70 字符），描述写清「为什么改、怎么测的、有无破坏性变更」。

## E2E 测试惯例

E2E 脚本（带 `subprocess` 调真实 CLI + 浏览器自动化）放在 `/tmp/skillsync_*_e2e.py`，不进仓。脚本末尾必须 cleanup 还原现场并在输出里打印还原结果。涉及破坏性造景（改 canonical 文件、登记临时 catalog 条目）时，先 `shutil.copy2` 备份，测完恢复。

## 行为准则

参与本项目即视为同意遵守 [Code of Conduct](CODE_OF_CONDUCT.md)。恶意骚扰、人身攻击、歧视性言论会被立即移除。

## 安全报告

发现安全漏洞请**不要**在公开 issue 提交，按 [SECURITY.md](SECURITY.md) 流程私下报告。
