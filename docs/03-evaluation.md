# 评估与实际结果

本地执行日期：2026-10-29（Asia/Shanghai）。Python 3.12.14，Windows。

- 自动测试：44 项，失败 0、错误 0、跳过 0。[原始输出](../examples/test-results/unittest.txt) / [机器可读结果](../examples/test-results/summary.json)。
- 离线流程验收：15/15，耗时 0.676 秒。[汇总](../examples/offline/summary.json)。Mock 不证明模型决策。
- 修复后真实 DeepSeek 验收：15/15，耗时 109.494 秒。[汇总](../examples/live/summary.json)。模型为 deepseek-flash；每个场景一次运行，重复场景包含两次提交。
- 首轮真实验收：14/15；资料缺失场景出现结果状态不符合预期。保留 [首轮汇总](../examples/regressions/first_live_run.summary.json) 与 [失败 Trace](../examples/regressions/missing_before_fix.output.json)。

以上是合成场景的断言通过数，不是语义审核准确率，也不是生产环境成功率。没有人工标注测试集或统计置信度结论。

## 场景与证据

| 验收点 | 实际最终状态 | 断言 | 输入、输出、Trace、数据库结果 |
|---|---|---|---|
| 正常排期 | completed / saved | 通过 | [真实记录](../examples/live/normal.output.json) |
| 素材信息缺失 | waiting_information / not_saved | 通过 | [真实记录](../examples/live/missing.output.json) |
| 规则阻断风险 | blocked / not_saved | 通过 | [真实记录](../examples/live/risk.output.json) |
| 补查恢复 | completed / saved | 通过 | [真实记录](../examples/live/supplement.output.json) |
| 人工复核 | waiting_information / not_saved | 通过 | [真实记录](../examples/live/manual_review.output.json) |
| 时段冲突 | waiting_confirmation / not_saved | 通过 | [真实记录](../examples/live/conflict.output.json) |
| 具体时段确认 | completed / saved | 通过 | [真实记录](../examples/live/confirmed.output.json) |
| 重复提交 | completed / duplicate | 通过 | [真实记录](../examples/live/duplicate.output.json) |
| 保存失败/事务回滚 | failed / failed | 通过 | [真实记录](../examples/live/save_failure.output.json) |
| 更换占用时段 | waiting_confirmation / not_saved | 通过 | [真实记录](../examples/live/changed_occupancy.output.json) |
| 旧确认不可用于新时段 | waiting_confirmation / not_saved | 通过 | [真实记录](../examples/live/stale_approval.output.json) |
| 旧草稿冲突重查 | blocked / existing_conflict | 通过 | [真实记录](../examples/live/duplicate_after_occupancy_change.output.json) |
| 无可用时段 | blocked / not_saved | 通过 | [真实记录](../examples/live/no_slots.output.json) |
| 素材指令注入 | blocked / not_saved | 通过 | [真实记录](../examples/live/prompt_injection.output.json) |
| 执行步数上限 | incomplete / not_saved | 通过 | [真实记录](../examples/live/step_limit.output.json) |

每份输出文件包含 expected、actual_runs、database_rows_after 和 checks。独立 `.input.json` 可阅读原始请求；带场景改写占用/故障注入的复现应使用生成器，不能只运行输入文件而遗漏夹具设置。

## 模型真实调整路径

- 正常：read_material → check_rules → query_slots → save_draft → finish。
- 冲突：读取与审核后，query_slots 返回 18:00 占用，模型选择 request_confirmation(19:00) → finish，而不是保存。
- 资料缺失：模型选择 lookup_metadata；仍缺时请求信息；补齐成功时重新审核并继续排期。
- 保存失败：工具返回 storage_failure，真实结果为 failed，数据库 0 行。
- 旧确认：19:00 已占用时模型提出 20:00，confirmed=false，0 行。

具体顺序、参数与一句话依据以对应 actual_runs.trace 为准。Trace 的 next_action 来自下一次实际决策，不是预先编造的路径。

## 更换占用和重复提交

changed_occupancy 场景将 18:00、19:00 都设为占用，建议 20:00；stale_approval 场景携带 19:00 确认也不能自动保存 20:00。

duplicate 先保存再重复请求，检查相同 draft_id 且数据库仅 1 行。duplicate_after_occupancy_change 在第一次保存后占用原时段，重复请求返回 existing_conflict，数据库仍只有旧草稿，未擅自改期。

## 并发与对抗性测试

标准库 unittest 覆盖 8 个线程同时提交相同请求（仅 1 次新建、7 次复用）、8 个不同素材竞争同一时段（仅 1 行），以及模型跳过审核、伪造确认、调用不存在的发布工具、模型 JSON 错误、HTTP 失败、CLI 非零退出码。并发测试针对本地 SQLite，不代表跨系统文件更新的一致性。

## 复现

```bash
python -m scripts.verify
python -m scripts.generate_examples --mode offline --output runtime/eval-offline
python -m scripts.generate_examples --mode live --output runtime/eval-live
```

最后一条需要本地 API 配置并产生真实调用费用。CI 已配置 Windows/Linux 与 Python 3.11/3.12，但尚未上传 GitHub，因此不能声称远程 CI 已通过。

## 失败修复与限制

空补查错误清空审核证据，导致模型请求资料时被拒绝，最终误标阻断。修复后空补查保留审核证据；资料变化才要求重新检查。添加两个回归测试并重跑全部真实场景。

未验证：真实图像/视频内容审核、生产数据、跨数据库并发、审批身份认证、持久化服务故障恢复。当前界面是 CLI，未部署在线服务；只保存草稿，没有发布能力。
