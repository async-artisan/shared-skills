# 计数、评估与最终核验

仅在宿主注入状态、需要登记评估／建议／回应或排查计数器时读取。业务提醒条件以 [SKILL.md](../SKILL.md) 为准。

## 计数与检查点

`PostCompact(auto)` 每次增加压缩次数；手动压缩、恢复、注入次数和聊天轮数均不计数。同回合可发生多次自动压缩。已识别子 Agent 的事件跳过。

压缩数达到提醒冷却阈值且没有用户静默或指定节点时，需要一次评估。`next_compaction` 使同一压缩点不在每轮重复检查；`next_turn` 在下一回合重评。新的真实压缩会使旧评估过期。实质阶段和指定业务节点由 Agent 识别，Hook 不扫描历史或猜阶段。

`SessionStart`／`UserPromptSubmit` 注入状态并登记宿主真实 `turn_id`。`Stop` 检查本轮待展示建议，以及到期压缩点是否缺少评估；缺失时最多请求补漏一次，两类缺失共用额度。宿主已标记 `stop_hook_active` 时不再发起补漏。跨回合不能用旧最终答复或旧评估冒充本轮完成。

## 命令

使用宿主提供且已核实的 session_id、turn_id；没有标识不能猜。Windows 使用 PowerShell 7：

```powershell
& '<PYTHON_EXE>' -X utf8 '<SKILL_DIRECTORY>/scripts/compaction_reminder.py' --state-dir '<CODEX_HOME>/state/project-handoff' --session-id '<当前任务ID>' --action status
```

先将 `<PYTHON_EXE>`、`<SKILL_DIRECTORY>`、`<CODEX_HOME>` 替换为本机实际 Python 程序、技能目录及 Codex 配置目录。`status` 只读，不建锁、不写回。以下动作替换上面的 `--action status`，并补参数：

| 动作 | 参数 |
| --- | --- |
| 暂缓／不适用 | `--action evaluate --turn-id '<当前回合ID>' --outcome defer --note '<具体原因>' --next-check next_turn`；不适用用 `skip`，同阶段或不适用用 `next_compaction` |
| 首次次数提醒 | `--action prepare --turn-id '<当前回合ID>' --reason count --stage-key '<稳定业务阶段>' --safe --has-next --notice '交接建议：<具体原因、下一步和授权动作>'` |
| 阶段提醒 | 上行 reason 改 `stage`，加 `--benefit` |
| 新混淆 | reason 改 `confusion`，加 `--benefit --cause-key '<已纠正问题标识>'` |
| 指定节点到达 | reason 改 `checkpoint`，加 `--checkpoint-key '<已登记节点>'`；节点真实到达由 Agent 核实 |
| 已在进度消息展示 | `--action notified --turn-id '<展示回合ID>' --proposal-id '<prepare返回的id>' --channel commentary`；不增加正式次数或启动冷却 |
| 明确暂不交接 | `--action respond --turn-id '<回应回合ID>' --proposal-id '<建议id>' --response continue` |
| 用户指定节点 | 上行 response 改 `defer`，加 `--checkpoint-key '<指定节点>'` |
| 静默／恢复 | `--action respond --turn-id '<回应回合ID>' --response mute` 或 `resume`；仅用户明确要求时使用 |
| 完整交接批准 | 回应参数用 `handoff`；仅停止提醒，业务交接仍按附页核验授权 |
| 建议失效 | `--action cancel --turn-id '<当前回合ID>' --proposal-id '<建议id>'`；记录本次跳过，不能伪装为用户拒绝 |

`note` 不超过 160 字，保留必要理由，不存用户原文或敏感信息。`notice` 是 20–600 字的一段实际建议。`safe`、`has-next`、`benefit` 必须有事实依据。`prepare` 返回 `prepared: true` 才能展示；失败后不能当作已有评估。评估为暂缓或不适用时，关闭尚未最终展示的旧建议，避免恢复后误补。

最终展示不使用人工 `notified --channel final`，该路径已拒绝。`Stop` 从宿主提供的最终文本核对正文首段是否为当前建议原段，忽略引用／代码示例；核验成功才计数。同一建议重复结束事件不重复计数。没有可用 Stop 时只做人工自检并报告最终核验未知，不补写“已核验”状态。

## 状态兼容与成本

每个 session_id 对应 state-dir 中一个 SHA-256 文件名，保存压缩计数、最近评估、当前建议、用户回应、去重信息及本轮补漏状态；不保存完整聊天。版本 3 将“提醒次数”限定为最终文本核验次数。旧版可能计入进度提醒：保留旧计数快照，只有可从单条记录确定时换算；其他历史总数标为未知，不把压缩序号或未保留的历史猜成最终提醒次数。真实压缩数不回滚。单次 `status` 的兼容转换只在内存中进行，下次正常写入才保存升级。

脚本不联网、不调用模型，不新建业务任务。普通检查不增加模型轮次；仅遗漏时，Stop 可能增加一次正常模型补漏回复。不得声称零额外用量或零漏报。Hook 没运行、执行失败或业务判断有误时，程序无法保证提醒发生。

## 部署与回退

Hook 模板见 [codex-hooks.example.json](../hooks/codex-hooks.example.json)。替换模板路径后，合并到用户 `hooks.json`，保留原有 Hook；旧版缺少 Stop 条目时需单独审阅并完成原生信任。配置或信任需要变更时另按授权执行；不能绕过宿主原生信任。文件、模拟事件、真实宿主事件、最终文本和用户界面可见性是不同证据。

回退代码时保留已增加的真实压缩计数，不覆盖运行状态为旧快照。旧版脚本不识别版本 3 时，需先备份当前状态并核对字段兼容性，再制定迁移方案；不能只降低版本号或用旧快照覆盖新计数。

[官方 Stop 接口](https://learn.chatgpt.com/docs/hooks#stop)提供 `turn_id`、`last_assistant_message` 和 `stop_hook_active`；`decision: block` 会请求继续处理，不是永久阻断任务。配套脚本不调整压缩设置、不增加定时任务。
