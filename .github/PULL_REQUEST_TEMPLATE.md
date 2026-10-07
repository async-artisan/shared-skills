## 摘要

<!-- 一句话说明本 PR 做了什么、为什么 -->

## 变更类型

- [ ] 新功能（feat）
- [ ] 缺陷修复（fix）
- [ ] 重构（不改变外部行为）
- [ ] 文档/测试/CI

## 自检清单

- [ ] 改代码前读过相关源码，理解分层（core → store → undo → 业务 → cli → web）
- [ ] 同一文件多处编辑串行执行，写完复核落盘片段
- [ ] 新增/改动逻辑有 `tests/unit/` 单元测试（`unittest`，不引入 pytest）
- [ ] 不引入第三方依赖（仅 Python 3 标准库）
- [ ] 写操作三件套齐备：UI `confirm` + 服务端 `Tx` 逆操作 + `audit()` 留痕
- [ ] DOM id / `window.*` 函数名 / API 路径走「新增」而非「改名」
- [ ] 机器本地数据（`registry/audit.logl`、`undo/`、`translate-cache.json`、`state.yaml`）未被纳入 git 索引
- [ ] 中文 commit message（`feat(skillsync): …` / `fix(web): …`）
- [ ] 本地跑过 `python3 -m unittest discover -s tests/unit -v` 全绿

## 测试

<!-- 说明跑了哪些测试，结果如何；若有破坏性造景，说明现场如何还原 -->

## 破坏性变更

<!-- 若有 DOM id/API 路径/默认行为变更，明确列出迁移路径；若无写「无」 -->

## 关联 issue

<!-- #123 -->
