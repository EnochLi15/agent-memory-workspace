# 实验复现协议

配置固定在 `configs/experiments.json`。执行前需要 `make init build`、公开数据准备、启动本地 Ollama。所有质量对照使用相同分组划分、Answer 提示词、模型和 32 条/约 6000 token 最终预算。不同模型输出存在采样差异；不把一次分数波动当成确定提升。

```sh
python3 scripts/run-experiment.py --campaign dev-v5 --profile U3 --port 8100
python3 scripts/run-experiment.py --campaign dev-v5 --profile B0 --port 8101
python3 scripts/run-experiment.py --campaign dev-v5 --profile B1 --port 8102
python3 scripts/run-experiment.py --campaign dev-v5 --profile B2 --port 8103
# U3 完整灌入结束后，检索消融复用相同记忆。每个 chunk 仍通过 HTTP 验证幂等回执。
python3 scripts/run-experiment.py --campaign dev-v5 --profile no_hop --reuse-ingestion U3 --port 8104
```

`artifacts/<campaign>/` 保存实际服务配置、服务日志和模型调用记录；`eval/artifacts/<campaign>-<profile>-<benchmark>/` 保存 HTTP、回答、判分、指标与版本清单。配置中排除密钥。断点续跑拒绝代码版本、配置、数据或提示词变化，避免混合不同实验。

U0 使用固定原版；U1 用 `service/baseline/prepare-u1.mjs` 机械拆出写入/检索方法，方法体 hash 与原版相同，`parity.ts` 另行验证运行行为。两者通过 `eval/scripts/streaming-gateway.py` 的本地 8767 传输桥接访问远端。桥接保持模型、消息和参数，仅处理非流式网关断连问题。原版 embedding provider 没有候选的 nomic 前缀，这一差异保留并报告。

U2 在候选的事务、来源和生命周期上使用上游评分函数及语义候选门限，关闭关系扩展和 LLM 重排；U3 使用完整候选。它们是可执行的阶段对照，U2 不等于原版 mem0。

B0/B1 保留完整原文（包括标明 assistant 的文字），分别使用词法和词法/向量；B2 使用事实抽取，但关闭状态更新/遗忘。B0–B2 是能力不足的实验对照，不能作为参赛服务。B3 启用生命周期，B4 增加历史/as-of 查询视图和两跳扩展，B5 增加重排，B6 纳入模型产生的有来源反思/推断事实。反思索引复用事实索引并保留依赖，不另建无来源的总结。时间消融关闭查询时的历史/as-of 视图，原文时间和模型抽取结果保留；它不是删除所有时间信息的实验。

单项关闭 `no_lifecycle/no_raw/no_time/no_hop/no_rerank` 另行比较。生命周期关闭需要独立重新灌入（可复用 B2 的关闭生命周期存储）；其他检索配置可复用 U3 完整灌入结果。检索共享不会向评测器暴露数据库。

保留集运行使用 `--split test --upstream-judge --concurrency 6`，各基准 500 个唯一问题。LoCoMo 使用固定上游 refined Python Judge 和本地 Qwen3:14b Q4_K_M；MemOps 使用原始 rubric 的语义判分代理。由于正式平台抽样、Answer 参数和 rubric 映射未提供，这些结果属于公开复现，不能称为官方成绩。

早期 dev-v1–v4 的 MemOps 题号仅包含 question_pair_id，错误合并了不同 evaluation_setting 的问题。v5 起题号包含 setting 和原始序号，适配、划分与 runner 均校验唯一性。旧结果保留作故障记录，不与修复后的准确率直接比较。
