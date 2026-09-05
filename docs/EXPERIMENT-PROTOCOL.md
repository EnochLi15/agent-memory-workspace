# 实验复现协议

配置固定在 `configs/experiments.json`。执行前需要 `make init build`、公开数据准备、启动本地 Ollama。所有质量对照使用相同分组划分、Answer 提示词、模型和 32 条/约 6000 token 最终预算。不同模型输出存在采样差异；不把一次分数波动当成确定提升。

```sh
python3 scripts/run-experiment.py --campaign dev-v6 --profile U3 --port 8100
python3 scripts/run-experiment.py --campaign dev-v6 --profile B0 --port 8101
python3 scripts/run-experiment.py --campaign dev-v6 --profile B1 --port 8102
python3 scripts/run-experiment.py --campaign dev-v6 --profile B2 --port 8103
# U3 完整灌入结束后，检索消融复用相同记忆。每个 chunk 仍通过 HTTP 验证幂等回执。
python3 scripts/run-experiment.py --campaign dev-v6 --profile no_hop --reuse-ingestion U3 --port 8104
```

`artifacts/<campaign>/` 保存实际服务配置、服务日志和模型调用记录；`eval/artifacts/<campaign>-<profile>-<benchmark>/` 保存 HTTP、回答、判分、指标与版本清单。配置中排除密钥。断点续跑拒绝代码版本、配置、数据或提示词变化，避免混合不同实验。

U0 使用固定原版；U1 用 `service/baseline/prepare-u1.mjs` 机械拆出写入/检索方法，方法体 hash 与原版相同，`parity.ts` 另行验证运行行为。两者通过 `eval/scripts/streaming-gateway.py` 的本地 8767 传输桥接访问远端。桥接保持模型、消息和参数，仅处理非流式网关断连问题。原版 embedding provider 没有候选的 nomic 前缀，这一差异保留并报告。

U2 在候选的事务、来源和生命周期上使用上游评分函数及语义候选门限，关闭关系扩展和 LLM 重排；U3 使用完整候选。它们是可执行的阶段对照，U2 不等于原版 mem0。

B0/B1 保留完整原文（包括标明 assistant 的文字），分别使用词法和词法/向量；B2 使用事实抽取，但关闭状态更新/遗忘。B0–B2 是能力不足的实验对照，不能作为参赛服务。B3 启用生命周期，B4 增加历史/as-of 查询视图和两跳扩展，B5 增加重排，B6 纳入模型产生的有来源反思/推断事实。反思索引复用事实索引并保留依赖，不另建无来源的总结。时间消融关闭查询时的历史/as-of 视图，原文时间和模型抽取结果保留；它不是删除所有时间信息的实验。

单项关闭 `no_lifecycle/no_raw/no_time/no_hop/no_rerank` 另行比较。生命周期关闭需要独立重新灌入（可复用 B2 的关闭生命周期存储）；其他检索配置可复用 U3 完整灌入结果。检索共享不会向评测器暴露数据库。

保留集运行使用 `--split test --upstream-judge --concurrency 3`，各基准 500 个唯一问题。LoCoMo 使用固定上游 refined Python Judge 和本地 Qwen3:14b Q4_K_M；MemOps 使用原始 rubric 的语义判分代理。由于正式平台抽样、Answer 参数和 rubric 映射未提供，这些结果属于公开复现，不能称为官方成绩。

早期 dev-v1–v4 的 MemOps 题号仅包含 question_pair_id，错误合并了不同 evaluation_setting 的问题。v5 起题号包含 setting 和原始序号，适配、划分与 runner 均校验唯一性。旧结果保留作故障记录，不与修复后的准确率直接比较。

## 当前实际复现批次

生产源码冻结于 910b3dd；之后测试准备和 baseline 适配器修正没有改变生产 src tree。主开发集为 dev-v6，最终公开保留集为 holdout-v2。原版/机械拆分对照使用 baseline-v7，修复仅涉及 HTTP 同 ID 的在途请求合并与同用户串行，旧 dev-v6 U0/U1 失败批次保留并标为 superseded。

```sh
python3 scripts/run-experiment.py --campaign baseline-v7 --profile U0 --port 8105 --concurrency 1
python3 scripts/run-experiment.py --campaign baseline-v7 --profile U1 --port 8106 --concurrency 1
python3 scripts/run-ablation-suite.py --campaign dev-v6 --port 8104 --concurrency 1
# 每批次运行目录必须是新的；以上为复现参数示例，不要在已存在目录上直接重跑。
cd eval
python3 scripts/compare-runs.py --campaign dev-v6 --include-campaign baseline-v7 --output artifacts/comparisons/dev-v6
```

配对报告拒绝不匹配的数据、模型或 Answer/Judge 提示词；两批次同名配置会报歧义，防止隐式选分。所有原始错误保留在计划分母中，共同完成判分的子集另列条件性诊断，不替代完整题数结果。

报告同时生成B0→B1→…→B6的逐阶段配对和U0→U1的重构质量对照，并保存每份输入文件及统计脚本的SHA256。B6与U3使用相同完整候选配置和相同灌入记忆，属于重复运行；它们的差异不能解释为新增组件收益。已完成的重复性核查见总控 `reports/development-repeatability.json`。固定的seed用于数据选择和统计重采样，远端模型内部采样和别名背后的权重版本不受该seed控制。

统计报告保留原始按sample区间，另以经过hash校验的数据集group_id计算背景整体重采样区间。MemOps开发集5个sample来自3个背景，保留集54个sample来自43个背景；LoCoMo分别为2个和8个独立对话。最终解释优先采用背景分组区间，不能把同背景操作变体或全部题目视为独立样本。compare-runs在找到hash匹配的划分文件时自动生成此补充；否则明确标记不可用。主保留集可单独执行：

```sh
python3 scripts/grouped-statistics.py --run-id holdout-v2-U3-locomo --data .data/locomo-test.json
python3 scripts/grouped-statistics.py --run-id holdout-v2-U3-memops --data .data/memops-test.json
```

这些命令只接受已完成的运行。20个完全相关样本归为2个背景的回归用例验证了重采样单位，另有数据hash变化拒绝测试；它们与8项Node评测器测试一起由npm test执行。

对已完成运行，`scripts/analyze-errors.py --run-id ID` 生成完全基于保存证据的错误阶段与来源覆盖清单。`node --env-file=../.env scripts/diagnose-answers.mjs --run-id ID --data PATH` 额外调用模型生成错误假设；假设不代表经验证的因果归因，引用无效或模型失败单独记录。

`python/upstream/memops/operation_metrics.py` 保留原版判分逻辑。通过 `.venv/bin/python scripts/memops-diagnostic.py --run-id ID` 在已配置环境中分析保存答案，输出 lifecycle 子指标，且不访问记忆服务或重做 Answer。该口径和本地 rubric 代理分开报告；开发集中相同答案已观察到明显判分分歧，详见 `reports/development-judge-comparison.json`（总控仓库）。

无说话人标签敏感性实验为 dev-v5-unlabelled-U3-locomo，匹配旧 dev-v5-U3-locomo 的生产源码与模型；两者输入 hash 有意不同。它仅说明该输入转换的敏感性，不能充当最新候选的同输入算法对比。
