# PR 合入与本地代码整合验证（2026-09-08）

已按用户要求合入 workspace PR #2、service PR #3，拉取到本地主分支，并在保留原有本地修改的基础上修复二审问题。service、eval 的实现分别在独立仓库提交，工作区通过 submodule 固定版本。

## 版本与变更

- workspace PR #2 合并：`afadad1b279a02ca959c5fb1a7e026520995ff91`。
- service PR #3 合并：`c5ad49dddad3c3e5c4d99093565e85fa26c8049c`；审查及离线对照的 PR head 为 `53b01bd36a464ab57efe58c41bcea0800ae1c92c`。
- service 本地故障恢复及历史证据修复：`f1f41e42153fd41ee15e22fe9a17d24f1d25df45`。
- service 遗忘安全修复：`6ea38670470fbbead5727c86200bccc1e8e96487`。
- service 最终版本：`6381bef2d705c3bc63b3616d529149d947a171f1`，补充被更正转述的历史追踪边界。
- eval Judge 生命周期：`7a2ecb42976220e70ff450d30c9569552a8ee1ab`。

| 二审问题 | 修复与验证边界 |
| --- | --- |
| 两个不同属性具有相同数字时，第二条遗忘命令错误复用第一条的目标 | 显式属性命令正常解析，仅明确的数字代指退役句式可以复用前文绑定。分别覆盖一次删除与两次删除。 |
| 相同相邻词或姓氏导致新人物、新事实被误删 | 绑定保留全部限定词；部分重叠仅在明确追溯/移除旧记忆时参与判断，并排除命名不同实体的语句。覆盖长姓名、姓氏、小写姓名、带回顾前缀的新实体、普通短语，以及应该被阻止复活的旧内容。 |
| 同一请求中新建后遗忘的值仍进入事件说明 | 在目标变更前保留临时原值快照，对已存储事实及本次待写入事实统一执行说明字段过滤。持久化 marker 保持散列表示。 |

同时保留并整理了本地已有的分片遗漏/内部预算故障降级、有限次数的修补越界重试、JSON 键名语法恢复、供应商错误码审计、历史说法及时间表达修正。模型给出明确的语义拒绝、请求被外部取消、被遗忘内容及其无效依赖仍不被放行。真实 SSE 与 HTTP 集成测试验证了语法恢复不会改写拒绝判定、400 不会盲目重试、降级回执幂等和后续请求恢复。

## 联合验证

在 service `6ea3867` 完整代码上执行 `make test`：

| 检查 | 结果 |
| --- | --- |
| service 构建及 Node 测试 | 507/507 |
| eval 构建及 Node 测试 | 24/24 |
| eval Python 测试 | 17/17 |
| workspace Python 测试 | 41/41 |
| 本地启动配置 Node 测试 | 3/3 |
| 独立临时实例的真实 HTTP 契约 | 12/12 |
| 优先评测清单重建 | manifest 与 4 份数据的 SHA256 全部相同 |

本机运行 Node 25.8.2；仓库 CI 使用 Node 24.18.0。工作区 `make test` 已纳入全部 `test_*.py`，包含 Judge 进程归属与退出故障注入。

远端验证均通过：[service `6381bef` CI](https://github.com/EnochLi15/agent-memory-service/actions/runs/34241176228) 包含依赖安装、构建、全量测试及 Docker 镜像构建；[eval `7a2ecb4` CI](https://github.com/EnochLi15/agent-memory-eval/actions/runs/34239805641) 包含构建、测试及 Python 编译检查。

后续真实历史回放发现，用户转述朋友薪资的旧说法带有 `quoted` 模态，即使已被更正，也被初版历史查询规则过度排除。`6381bef` 仅允许明确追问历史消息时将它作为被更正的说法展示，保留 hypothetical、as-of、遗忘和无效依赖过滤；不修改排序。定向回归改用实际事故中的 quoted 条件，冻结整库同一查询向量的对照恢复了缺失的更正事件。最终版本重新构建，service 全量仍为 **507/507**，真实 HTTP 契约仍为 **12/12**。

最终单题回放使用的冻结产物 `priority-quality-history-20260908/service-dist` 与本地 `6381bef` 构建的 **46 个 JavaScript 模块 SHA256 全部相同**，因此可以对应到最终服务代码。它与前述初版 92 题候选分开记录。

这道薪资历史题使用完整 B01_update 历史、50 个原幂等回执和原 Answer/Judge 配置，通过标准评测器取得 **1/1 正确**：恢复 145K → 152K → 148K 的轨迹，并将 155K 保持为尚未生效的提议。没有重新写入模型调用或替换原 92 题判分。[单题回放报告](priority-history-followup-20260908.json) 记录了评测器原未提交补丁被完整提交为 `7a2ecb4` 的内容等价校验。

```sh
make test
node scripts/probes/contract-isolated.mjs
python3 scripts/select-priority-benchmarks.py
node scripts/probes/replay-offline-operations.mjs \
  --dataset eval/.data/priority-20260908-v1/memops.json \
  --output artifacts/offline-replay-new.json
```

所有探针使用独立临时存储；离线回放输出拒绝覆盖已有报告。私有模型输入、追踪、数据库、环境密钥与运行产物未提交。

## 全部优先 MemOps 历史的离线对照

95 个样本包含 472 道题、51,108 条消息，按评测器的 20 条消息/2,000 词限制产生 4,898 次写入。每次写入均通过真实 Extractor/TenantStore；单次失败不会提前结束样本后续写入。该检查不调用模型，不回答问题或判分。

| 版本 | 成功写入 | 拒绝 | 新增失败 |
| --- | --- | --- | --- |
| PR head `53b01bd` | 4,889/4,898 | 9 | — |
| 安全修复 `6ea3867` | 4,889/4,898 | 9 | 0 |

两次结果的失败样本、会话、分块及错误码完全一致。8 次为 `OPERATION_INTENT`，1 次为 `OPERATION_TARGET`；这些基线拒绝仍然存在。明细、数据校验值与原始产物散列见 [离线对照 JSON](post-merge-offline-regression-20260908.json)。这不是全部写入通过，也不能据此推断 enhanced 模式质量分数。

## 独立质量回放

另一任务在独立冻结的编译产物和历史数据库上回放 LoCoMo 72 题与 MemOps 20 题。冻结时间早于本轮遗忘安全修复，因此其结果不能作为 `6ea3867` 的端到端质量成绩，也不能代表公开优先清单的 972 题总分。原始运行、失败与判分保留；诊断及可追溯性见 [历史证据与恢复报告](priority-quality-fixes-20260908.md)。

初版候选完整结果为 **75/92**，原对照 **69/92**：LoCoMo 56/72（原 51/72），MemOps 19/20（原 18/20），9 题改善、3 题退步。LoCoMo 一题在检索成功后 Answer 出现 `fetch failed`，仍按 0 保留在分母；没有补分。最终 `6381bef` 的单题 1/1 单独呈现，不拼接到初版总分。题目级差异与全部输入校验见 [92 题比较报告](priority-quality-comparison-20260908.json)。
