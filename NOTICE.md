# NOTICE — 许可范围与内容边界

## 仓库定位

本仓库开源的是 **skill-hub 工具与仓库骨架**（一个跨平台 Agent 技能的中心枢纽管理工具）。`skills/` 目录是使用者的**个人技能库**，属于私有内容，不随本仓库开源。

## 许可覆盖范围

[MIT License](LICENSE) 仅适用于以下随仓库分发的代码与配置：

- `tools/skillsync/` — skillsync CLI、Web 控制台及其测试
- `bin/` — 启动包装脚本
- `platform/`、`manifests/`、`.claude-plugin/`、`.codex-plugin/`、`.agents/` — 平台插件骨架与清单模板
- `registry/platforms.example.yaml` — 自定义平台示例配置
- 仓库根的文档（`README.md`、`CONTRIBUTING.md` 等）

## 不授权的内容

以下内容属于使用者私有，**MIT 许可不及于它们，不授予任何权利**：

- `skills/` — canonical 技能实体（每个 `skills/<slug>/SKILL.md` 及配套文件），被 `.gitignore` 排除，不随仓库分发
- `registry/catalog.yaml` — 个人技能台账（slug、平台清单、采纳时间等），被 `.gitignore` 排除，不随仓库分发；首次运行时从随仓的 `registry/catalog.example.yaml` 自动复制初始化
- `registry/state.yaml`、`registry/audit.logl`、`registry/undo/`、`registry/translate-cache.json` — 本机状态、留痕与缓存，不入库

若你从其他渠道（如 ClawHub、各平台市场）获得技能内容，其使用须遵循对应来源的许可条款，与本仓库无关。

## 第三方商标

工具文档中可能以指示性方式提及 Codex、Claude、WorkBuddy、TRAE、ClawHub 等第三方产品名称，这些名称分属各自所有者的商标，本仓库与上述方无隶属或背书关系。

## 贡献者

贡献者署名通过 git 提交记录保留。提交 PR 即视为同意其对**工具代码**的贡献按 MIT 许可发布；技能内容不经过本仓库的 PR 流程。
