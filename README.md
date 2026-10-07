# shared-skills

跨平台 Agent Skill 单一事实源仓。一份 `skills/<slug>/SKILL.md`，通过软链下发到
Codex / Claude / WorkBuddy / TRAE 四个平台，消除多平台手工复制导致的内容分叉。

- 内容标准：open agent skills 标准（`SKILL.md` + YAML frontmatter，必需字段仅 `name`/`description`）
- 零第三方依赖：Python 3 标准库
- 安全原则：只增不删；替换实体目录前一律改名为 `.bak-<时间戳>`；默认 dry-run

## 目录

```text
skills/            canonical 技能（唯一事实源）
registry/
  catalog.yaml     技能台账：platforms / tools_required / depends_on / description_zh
  state.yaml       本机状态：忽略名单、最近 apply
  audit.logl       操作审计（JSON Lines，由工具追加）
platform/          平台私有件（hooks/commands/MCP，不跨平台共享）
.claude-plugin/    Claude 原生市场/插件清单
.codex-plugin/     Codex 插件清单
.agents/           Codex 市场清单
manifests/         WorkBuddy 等市场模板
tools/skillsync/   CLI 源码
```

## 常用命令

```bash
bin/skillsync doctor                 # 只读对账：分叉/未登记/失效软链
bin/skillsync adopt codex <slug>     # 采纳：复制进 skills/ 并登记 catalog（不动源目录）
bin/skillsync adopt codex <slug> --link --targets codex,trae
bin/skillsync migrate --from trae --all   # 批量采纳某平台未登记技能
bin/skillsync apply                  # 预演下发（dry-run）
bin/skillsync apply --write --link   # 落盘：软链到 catalog 声明的平台，副本先备份
bin/skillsync resolve trae <slug> --winner canonical  # 分叉裁决（两侧先备份）
bin/skillsync verify                 # canonical 自检
bin/skillsync ignore workbuddy "AI HOT"   # doctor 中忽略
bin/skillsync serve                  # 本机 Web 控制台（仅 127.0.0.1，自动开浏览器）
```

Web 控制台提供：总览看板、待采纳一键操作（选平台/预览）、分叉差异查看与裁决、
apply 预演/执行（二次确认）、只读技能库。页面不提供在线编辑，避免产生第二事实源。

平台目录位置：

| 平台 | 目录 |
| --- | --- |
| codex | `~/.codex/skills`（`.system/` 为内置，不对账） |
| claude | `~/.claude/skills` |
| workbuddy | `~/.workbuddy/skills` |
| trae | `~/.trae-cn/skills` |
