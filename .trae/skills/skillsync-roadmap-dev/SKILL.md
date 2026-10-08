---
name: skillsync-roadmap-dev
description: 按 roadmap 逐项开发 shared-skills 仓 skillsync 工具的功能。当用户要求继续路线图、开发 skillsync 新功能或在 shared-skills 仓实现 doctor/web/CLI 功能时使用。不用于其他项目。
---

# skillsync 路线图功能开发

在本会话已按此流程交付 7 项功能（远程导入、undo、增量扫描、批量预翻译、拉取更新、归档态、多机 git 共享流），每项独立提交。新功能沿用同一套路。

## 代码地图

仓库根 `/Users/qiujianjun/Projects/AI/shared-skills`（下称 REPO）：

- `tools/skillsync/skillsync/` — Python 包（仅标准库，零第三方依赖）
  - `core.py` — 路径常量（`REPO_ROOT` 可被 `SKILLSYNC_HOME` 重定向，E2E 隔离靠它）、平台定义、hash、frontmatter
  - `doctor.py` — 只读对账：`Finding` dataclass、`scan_all()`、`format_text()`；UI 卡片/汇总口径全由它的 summary 决定
  - `webapp.py` — 本机 Web 控制台 API（http.server，127.0.0.1）；POST 路由表在 `do_POST` 的 `routes` dict
  - `cli.py` — 子命令入口，`build_parser()` 注册
  - `store.py` — catalog/state 读写 + `audit()`；`undo.py` — `Tx` 逆操作收集器（`catalog_set`/`rm_tree`/`state_ignored` 等指令）
  - `gitsync.py` — 多机 git 流（sync-status/push/pull + 预检拒绝策略）
  - `web/index.html` — 单文件内联 CSS+JS（约 1400 行），Web UI 一律只改这个文件
- `bin/skillsync` — 启动包装；`registry/` — catalog.yaml（事实源，入库）+ state/audit/undo/translate-cache（机器本地，不入库）

## 固定流程

1. **需求确认**：路线图项若无书面原文，先用 AskUserQuestion 确认设计点（数据边界、命令面、远端形态），不要自行脑补。
2. **设计定稿后再动代码**；同一文件多处编辑必须串行，写完立即复核落盘片段。
3. **实现顺序**：底层（doctor/store/undo）→ 接口层（webapp/cli）→ UI（index.html）。py_compile 校验每个改动的 py 文件。
4. **验证留证**（用户硬性要求所有验证有痕迹）：HTTP E2E + 浏览器 E2E 脚本放 `/tmp/skillsync_*_e2e.py`，全程截图 `/tmp/*.png`，脚本末尾 cleanup 还原现场并在输出里打印还原结果。
5. **提交**：`git -C <REPO>` 显式定位（shell cwd 可能漂移）；只 add 本任务文件；中文 commit message（`feat(skillsync): 第 N 项xxx——一句话`）。

## 稳定性红线

- DOM id、`window.*` 函数名、API 路径一经发布不得变更，只能新增。
- 写操作必须：confirm 二次确认（UI）+ `undo_mod.Tx` 记录逆操作（服务端）+ `audit()` 留痕，三者缺一不可。
- `registry/` 下机器本地文件（state.yaml、audit.logl、undo/、translate-cache.json）与 `skills/` 的用户数据永远不进 commit。
- 金额/哈希等裁决不用浮点；对账口径改动必须同时考虑 doctor 的 summary、format_text 与 Web 卡片三处一致。

## E2E 造景经验（踩过的坑）

- 平台侧软链与 canonical 解析到同一目录，改 canonical 文件造不出 fork/broken-link 异常；造真实异常用「临时给已存在技能登记 catalog 条目 → missing-canonical」或新建临时技能，测完还原 catalog.yaml（先 `shutil.copy2` 备份）。
- 页面有 5s 轮询：`wait_until="domcontentloaded"` + `wait_for_selector`；`page.route(re.compile(r"fonts\.(googleapis|gstatic)\.com"), abort)`；`page.on("dialog", d => d.accept())`；URL 加 `?v=xxx` 绕缓存。
- chromium 路径：`/Users/qiujianjun/Library/Caches/ms-playwright/chromium-1243/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing`。
- UI 断言注意卡片 DOM 结构是数字在前、标题在后（`(\d+)\s*\n\s*需处理问题`）。

## 服务重启（webapp.py 改后必须，index.html 热生效）

```sh
PID=$(lsof -nP -iTCP:48920 -sTCP:LISTEN -t); kill $PID
cd <REPO>/tools/skillsync && PYTHONPATH=<REPO>/tools/skillsync \
  nohup /Library/Frameworks/Python.framework/Versions/3.10/bin/python3 -m skillsync serve --port 48920 --no-browser >> /tmp/skillsync-serve.log 2>&1 &
```

## 多机流（gitsync，已上线）

只同步事实源 skills/ + registry/catalog.yaml + 仓骨架；push 预检拒绝 .bak 残留、missing-canonical、落后远端、未合并冲突；pull 默认 --ff-only，分叉需显式 --rebase。给 gitsync 写测试用 `SKILLSYNC_HOME` 指向 /tmp 双机目录 + file:// 裸仓，绝不碰真实仓配置。
