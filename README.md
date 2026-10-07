# shared-skills

跨平台 Agent Skill 单一事实源仓。一份 `skills/<slug>/SKILL.md` 通过软链下发到 Codex / Claude / WorkBuddy / TRAE 四个平台，消除多平台手工复制导致的内容分叉。

[![CI](https://img.shields.io/github/actions/workflow/status/yesgooo/shared-skills/ci.yml?branch=main&label=CI)](https://github.com/yesgooo/shared-skills/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

## 功能特性

- **单一事实源**：canonical 技能目录 `skills/<slug>/SKILL.md`，所有平台通过软链共享同一份内容
- **只读对账**：`doctor` 实时扫描四平台与共享仓，分类所有差异（分叉、未登记、失效软链、仓外软链、元数据异常）
- **安全采纳**：复制平台目录进共享仓，**只增不删**；替换实体目录前一律改名为 `.bak-<时间戳>`
- **受控下发**：`apply` 默认 dry-run，落盘时实体目录先备份，分叉内容安全拒绝
- **分叉裁决**：`resolve` 时两侧都备份，胜出版本原位换成软链
- **撤销机制**：所有写操作记录逆操作指令（`undo`），失败则丢弃，支持按事务回滚
- **多机共享**：`sync-status` / `push` / `pull` 三个命令，预检拒绝 `.bak` 残留、`missing-canonical`、落后远端、未合并冲突
- **归档态**：catalog 标记 `archived`，已退役技能的异常自动降级为「不催办」
- **Web 控制台**：本机 127.0.0.1 控制台（总览看板、待采纳、问题分叉、操作历史、技能库）
- **离线翻译**（可选）：英文 `SKILL.md` 简介可批量预译为中文（仅预览，不回写文件）
- **零第三方依赖**：仅 Python 3 标准库

## 安装

shared-skills 是单仓自包含工具，无需 pip 安装。clone 即用：

```bash
git clone https://github.com/yesgooo/shared-skills.git
cd shared-skills
```

要求：Python 3.10+、macOS 或 Linux（软链需文件系统支持）。

可选：把启动包装放进 PATH，便于在任意目录调用 `skillsync`：

```bash
ln -s "$(pwd)/bin/skillsync" /usr/local/bin/skillsync
```

平台技能目录（首次使用前手动创建）：

| 平台 | 目录 |
| --- | --- |
| codex | `~/.codex/skills`（`.system/` 为内置，不对账） |
| claude | `~/.claude/skills` |
| workbuddy | `~/.workbuddy/skills` |
| trae | `~/.trae-cn/skills` |

`SKILLSYNC_HOME` 环境变量可重定向仓库根（用于测试或多机切换）。

## 自定义平台

不用 codex / workbuddy，只想同步千问办公、豆包工作等平台？写一份平台清单即可，**无需改代码**：

```yaml
# registry/platforms.yaml（复制 registry/platforms.example.yaml）
version: 1
platforms:
  qwen:
    label: 千问办公
    skills_dir: ~/.qwen-office/skills
  doubao:
    label: 豆包工作
    skills_dir: ~/.doubao-work/skills
    builtin_dirnames: [.system]   # 可选：需跳过的平台内置子目录
```

**查找顺序**（前者存在即整体生效，不做跨文件合并，改完重启 `serve` / CLI）：

1. 环境变量 `SKILLSYNC_PLATFORMS` 指向的文件
2. `~/.skillsync/platforms.yaml`（个人级，不经过仓库）
3. `<仓库>/registry/platforms.yaml`（仓库级，可随 fork 提交给团队共享）
4. 内置默认：codex / claude / workbuddy / trae（无配置时零变化）

规则与边界：

- `key` 只能用小写字母/数字/短横（如 `qwen-office`），会进入 catalog、命令行与页面徽章；控制台筛选器和徽章配色按配置自动生成
- `skills_dir` 支持 `~`、`$VAR` / `${VAR}`、Windows `%VAR%`；相对路径锚定仓库根
- 配置是**整体替换**：只想新增一个平台时，把仍在使用的内置平台也写回去
- 仅适用于「本地目录 + `SKILL.md`（YAML frontmatter）+ 接受软链」的平台；云端市场或打包上传类通道需要另行扩展下发方式，欢迎提 issue 讨论

## 使用示例

### 新机器一键上手

```bash
bin/skillsync bootstrap            # 扫描 + 下发已纳管软链 + 报告待采纳
bin/skillsync bootstrap --dry-run  # 预演，不真正创建软链
bin/skillsync bootstrap --no-apply # 只扫描报告，不下发软链
```

bootstrap 把新机器最短路径串成一步：把 `catalog` 里已纳管的技能软链下发到本机平台目录，并汇总待采纳技能与问题信号。采纳（把平台散装技能收进共享仓）仍需 `serve` 控制台或 `migrate` 人工确认来源。

### 只读对账

```bash
bin/skillsync doctor           # 概览：分叉/未登记/失效软链
bin/skillsync doctor --strict  # 存在问题时以非零码退出（CI 用）
bin/skillsync doctor --json    # 机器可读
```

### 采纳与下发

```bash
# 采纳 codex 平台的新技能进共享仓，并立即软链下发到 codex+trae
bin/skillsync adopt codex my-skill --link --targets codex,trae

# 批量采纳某平台全部未登记技能
bin/skillsync migrate --from trae --all --link

# 预演下发（dry-run）
bin/skillsync apply

# 落盘：软链到 catalog 声明的平台，实体副本先备份为 .bak-时间戳
bin/skillsync apply --write --link
```

### 分叉与归档

```bash
# 分叉裁决：canonical 与平台副本二选一（两侧先备份）
bin/skillsync resolve trae my-skill --winner canonical

# 归档技能（catalog 标记 archived，异常不再催办）
bin/skillsync ignore workbuddy "AI HOT"   # 单条豁免 doctor
```

### 本机 Web 控制台

```bash
bin/skillsync serve                    # 自动开浏览器
bin/skillsync serve --port 48920 --no-browser
```

控制台提供：总览看板、待采纳一键操作（选平台/预览）、分叉差异查看与拉取更新、
apply 预演/执行（二次确认）、操作历史与撤销、技能库只读浏览。页面不提供在线编辑，避免产生第二事实源。

### 多机 git 共享流

shared-skills 的所有事实源（`skills/` + `registry/catalog.yaml` + 仓骨架）通过 git 同步；机器本地数据（state/audit/undo/translate-cache）不跨机。

```bash
bin/skillsync sync-status          # fetch 后看 ahead/behind、变更分类、可执行结论
bin/skillsync push                # 预检 + 自动中文提交说明 + 推送（首次自动 -u）
bin/skillsync pull                # --ff-only；分叉需显式 --rebase；拉取后自动 doctor 对账
```

`push` 预检拒绝以下场景（exit 2，不做任何写操作）：未配置远端、落后远端、未合并冲突、`.bak-*` 残留、catalog 声明但 `skills/` 缺目录。`pull` 受跟踪文件脏改时拒绝。无 force/reset。

## 项目目录

```text
skills/            canonical 技能（唯一事实源）
registry/
  catalog.yaml     技能台账：platforms / tools_required / depends_on / description_zh
  state.yaml       本机状态：忽略名单、最近 apply（不入库）
  audit.logl       操作审计 JSON Lines（不入库）
  undo/            撤销事务记录（不入库）
  translate-cache.json  英译中磁盘缓存（不入库）
platform/          平台私有件（hooks/commands/MCP，不跨平台共享）
.claude-plugin/    Claude 原生市场/插件清单
.codex-plugin/     Codex 插件清单
.agents/           Codex 市场清单
manifests/         WorkBuddy 等市场模板
tools/skillsync/   CLI 源码
  skillsync/
    core.py        路径常量、平台定义、目录哈希、frontmatter 解析
    store.py       catalog/state 读写 + audit
    undo.py        Tx 逆操作收集器（rm_tree/catalog_set/state_ignored 等）
    doctor.py      只读对账：Finding/scan_all/format_text
    bootstrap.py   新机器上手：扫描→下发软链→报告待采纳
    adopt.py       采纳单个技能
    apply.py       软链下发
    resolve.py     分叉裁决
    gitsync.py     多机 git 流（sync-status/push/pull）
    webapp.py      本机 Web 控制台 API
    cli.py         子命令注册
    web/index.html 单文件 UI（内联 CSS+JS）
tests/unit/        单元测试（unittest，零依赖）
.github/           Issue/PR 模板 + CI workflow
```

## 测试

```bash
cd tools/skillsync && PYTHONPATH=. python3 -m unittest discover -s ../../tests/unit -v
```

E2E 测试（带真实 CLI subprocess + 浏览器自动化）放在 `/tmp/skillsync_*_e2e.py`，不进仓，本地按需运行。

## 贡献

欢迎 PR！开始前请先读 [CONTRIBUTING.md](CONTRIBUTING.md) 与 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。

核心约定：

- 零第三方依赖，仅 Python 3 标准库
- 写操作三件套：UI `confirm` + 服务端 `Tx` 逆操作 + `audit()` 留痕
- DOM id / `window.*` 函数名 / API 路径一经发布不得变更，只能新增
- 中文 commit message（`feat(skillsync): …` / `fix(web): …`）
- 单元测试用 `unittest`，不引入 pytest

## 许可证

[MIT License](LICENSE) © 2026 yesgooo_qjj

## 安全报告

发现安全漏洞请**不要**在公开 issue 提交，按 [SECURITY.md](SECURITY.md) 流程私下报告。
