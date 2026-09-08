# 优先评测写入故障诊断（2026-09-08）

评测 `priority-20260908-02` 已按用户要求停止；runner、service、LoCoMo 子进程均已退出，本任务创建的 Judge 适配器也已停止。原始数据、写入日志及 419 条 service_error 记录保留，未重判或改分，未重启整批评测。

## 证据与根因

5 个对话各有一条写入失败，合计使 419 道题无法进入问答判分。评测器采用整段对话灌入成功后才回答的策略，所以单条写入失败会影响该对话全部题目。

| 请求 | 现象 | 定位与处理 |
|---|---|---|
| conv-26 / session-3 | Edited repair fact is outside the affected sources | 补丁引用越过限定消息范围。校验本身正确，但调用方立即退出，没有用完剩余修补次数。新增具名错误类型，丢弃坏补丁、保留原提案与原 scope，在既有次数与时间预算内重试；反复越界仍拒绝。 |
| conv-41 / session-3 | Extraction shard omitted a participant | 模型没有返回分配给分片的全部消息组。格式不完整被包装成 EVIDENCE_VALIDATION，跳过了普通写入的离线降级。现在只把明确的遗漏消息组标为可降级能力故障；重复、外来组、显式语义拒绝仍不接受。 |
| conv-30 / session-15 | The operation was aborted due to timeout | 分片函数把模型内部预算取消包装成 EVIDENCE_VALIDATION。上层虽然预留了降级时间，却直接重抛。现在保留原始预算取消，由持有外层请求期限的调用方决定降级；外部请求取消仍禁止提交。 |
| conv-42 / session-3 | Evidence still fails semantic verification after repair | 原日志只有终态，没有保存完整模型响应与核验 findings。隔离重放结果见下，不把此错误直接改为成功。 |
| conv-43 / session-2 | A rejected proposal could not be repaired after a model or protocol failure | 表明提案先被语义核验拒绝，随后遇到模型或协议异常；原日志不足以恢复具体核验理由。隔离重放结果见下。 |

运行配置确认 `writeContinuation=false`、`sourceFirst=false`、`extractionWorkers=3`。因此，两个能力故障未降级并非误开严格配置，而是分片错误分类破坏了上层降级分支。

## 验证

- 修复前，3 个最小测试稳定复现日志中的原始错误，3/3 失败。
- 修复后，全服务测试 472/472 通过，包括新增 6 项：漏组降级、内部超时降级、越界修补恢复、重复越界仍拒绝、显式语义拒绝仍拒绝、外层取消不提交。
- 原始 HTTP 请求的完整消息做受控故障注入：conv-41 的 17 条消息在漏组后降级提交，conv-30 的 20 条消息在内部预算耗尽后降级提交；两者 SQLite revision 均为 1。此实验使用临时空库和注入模型故障，不冒充原模型输出的精确重放。
- 未修改语义核验提示词、证据有效性约束、评测分母、历史分数或整批运行配置。

自动测试原始输出：`reports/priority-write-fix-tests-20260908.txt`。原始输入注入结果：`reports/priority-original-input-replay-20260908.json`。

## 隔离模型诊断

使用停止后对应 tenant 数据的独立副本，对两条原始失败请求仅运行 prepare，不调用 commit，不产生新评测分数。输入、模型输出和反馈保存在本地 `artifacts/priority-write-diagnosis-20260908/`，追踪文件权限 0600。该诊断是新的随机模型运行，不能恢复原批次未保存的输出。

本次隔离结果：conv-42 准备成功，20 条事实，首轮核验无 findings；conv-43 准备成功，14 条事实，首轮指出“正在探索代言机会”和“正在考虑品牌”属于当前正在发生的行为，应将该行为标为 confirmed，而不是把它当作仅可能发生的行为。这里不意味着代言或品牌合作本身已经确认。修补后核验通过。两个请求均未提交，也未计分。

完整汇总见该目录的 `results.json`。整批评测保持停止；尚不能据局部修复断言 972 题全部写入成功。

## 预防

保留“输出协议/可用性失败”和“已确认的语义拒绝”的类型区分，避免多个模块把所有失败压成 EVIDENCE_VALIDATION。后续隔离诊断应继续启用已有的私有模型追踪，保存核验理由与坏补丁，便于精确重放；不应仅凭生成接口的 outcome=ok 认定提取内容通过校验。
