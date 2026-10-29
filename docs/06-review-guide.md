# 五分钟讲解与评审操作

## 项目描述

实现素材审核与发布排期 Agent，使用真实模型工具调用读取素材、审核风险、补查资料、查询时段并决定是否请求确认或保存草稿。执行器通过具体提案确认、SQLite 幂等与事务约束防止越权改期、重复保存和冲突；失败和超限有明确状态及 Trace。

这段只描述项目能力。个人贡献请根据 `04-ai-usage.md` 和自己后续实际复核补充，不将 AI 产出全部说成独立手写。

## 可直接演示的顺序

1. 正常：`python -m src --input examples/input_normal.json --db runtime/review.sqlite3`。
2. 重复：再次执行同一命令，draft_id 不变、draft_status=duplicate。
3. 冲突：`python -m src --input examples/input_conflict.json --db runtime/review.sqlite3`，19:00 只是建议，未保存。
4. 按输出 proposal_id 确认，保存后再次重复验证。
5. 保存失败：使用独立数据库并加 `--save-failure`，检查 failed 和回滚证据。
6. 看 `examples/live/stale_approval.output.json`：19:00 的旧确认不能授权 20:00。
7. 看 `examples/regressions/`：解释一次实际失败和对应修复。

本机无 PATH Python 时，使用根目录 `run.ps1`，例如 `./run.ps1 --input examples/input_conflict.json`。

## 必须能回答的问题

- 为什么这是真实 Agent？live 模式逐步调用模型选动作，再观察工具结果；offline 是明确标注的测试替身。
- 为什么模型说成功不算成功？最终字段从执行器/事务结果投影，模型不能写 saved 或 published。
- 为什么 request_id 不够？新 ID 可重复同一素材，因此还需要素材日期渠道唯一约束；另有时段唯一约束。
- 为什么不把 confirmed=true 传进去？授权必须绑定具体请求和替代时段，否则占用变化后会越权。
- 并发如何处理？SQLite 写事务串行化写入，唯一约束兜底；测试验证 8 个竞争请求只落一行。
- 边界在哪里？当前确认基于可信 CLI，外部文件不是原子排期源；生产需要认证审批和权威数据库版本控制。
- 15/15 是否代表准确率？不是，只是合成场景的一次验收通过数，没有代表性统计结论。
