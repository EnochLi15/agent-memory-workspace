截至 2026-09-09，本轮已让 B30 完成整段历史和问答，并将 B03 推进到第 44 块历史。已完成的评测和失败记录均保留，没有重判旧答案。

| 样本 | 历史写入 | 问答结果 | 当前问题 |
|---|---:|---|---|
| B30 / Forget | 51/51 成功 | 5 题全部判完，1/5 正确，仅 p4 通过 | 检索遗漏、时间回答越过证据推断、遗忘对象追溯不足 |
| B03 / Remember | 44/50 成功 | 尚未进入问答；6 题按 service_error 计零 | 第 45 块的来源核验在 JSON 后追加散文，解析失败 |

两项使用不同修补候选与真实历史前缀，属于恢复诊断，不是统一版本的整体成绩。圈定的 972 题仍没有完整得分。

本轮完成了三个主要写入修补：将模型已选中的跨范围删除目标拆为分别核验的操作；暂缓发布与历史删除边界冲突的本次可选聚合卡，保留原始事实、消息和旧记忆；拒绝把助手的句子式开场误识别为人名。来源引用修补和来源核验的类型契约也已纳入统一候选。统一基底为 `5ca2929d09e718c4aa5353460a1f9338ad0a9c30`，构建与 678 项测试通过，尚有下述已知问题。

| 真实单点验证 | add 耗时 | 结果 |
|---|---:|---|
| B30，第 30 块 | 58.79 秒 | 成功，revision 30→31；两个目标分别通过语义核验 |
| B03，第 34 块 | 4.48 秒 | 成功，revision 33→34；记录聚合卡暂缓标记 |
| B03，第 45 块 | 65.43 秒 | 失败；8 条修补后事实通过核验，最后的来源核验输出格式无效，revision 44 未变 |

B30 后续 20 次 add 全部成功，合计 244.29 秒，平均 12.21 秒。完整尾部评测约 405.65 秒；5 次 search 合计约 0.68 秒，其余约 160.63 秒包含回答、判分和评测开销，现有记录不能再准确拆成独立 Answer/Judge 耗时。

B30 的两个称呼问题需要 Patricia 的新状态。相关事实、原句和来源段落仍可见，但未进入实际返回的 32 条证据；这不是删除修补把新状态删掉。时间题需要的 December 证据也仍存在，但未返回，回答随后把另一人的 November 信息推给 Marc。遗忘对象题则缺少可公开检索的对象身份记录，需单独处理身份追溯与已删细节之间的边界。

下一步先处理 B03 的模型输出格式故障，保持原始判定和完整校验，成功后从真实回执续跑最后 5 块历史及 6 道题。随后用已记录的查询向量和检索阶段数据改进 32 条证据的选取，检查关键来源覆盖，并保留 A06 已知证据丢失反例作为回归约束。

详细记录：[B30 完整评测](/Users/enoch/Workspace/comp/agent-memory-workspace/reports/priority-B30-tail-qa-results-20260909.json)、[B30 错误归因](/Users/enoch/Workspace/comp/agent-memory-workspace/reports/priority-B30-tail-error-attribution-20260909.json)、[B03 尾部失败](/Users/enoch/Workspace/comp/agent-memory-workspace/reports/priority-B03-tail-qa-results-20260909-01.json)、[第 45 块最新验证](/Users/enoch/Workspace/comp/agent-memory-workspace/reports/priority-B03-segment45-results-20260909.json)、[统一修复基底](/Users/enoch/Workspace/comp/agent-memory-workspace/reports/priority-unified-repair-baseline-20260909.json)。
