截至 2026-09-09 06:05，查询范围候选的一次标准 16 题评测已完成，得分 10/16（62.5%）。152 次历史回执全部校验通过，检索、回答和判分为本次实际执行；没有新的历史写入或写入模型调用。已完成的旧评测和失败记录均保留。

| 样本 | 完整历史回执 | 本次问答结果 | 当前问题 |
|---|---:|---|---|
| B03 / Remember | 50/50 | 5/6，p1、p2、p4、p5、p6 通过；旧轮为 4/6 | p3 证据完整，回答漏写评分要求的职称 |
| B30 / Forget | 51/51 | 3/5，p3、p4、p5 通过；旧轮为 1/5 | p1 检索遗漏仍在，p2 遗忘对象追溯不足 |
| A06 / Forget | 51/51 | 2/5，与旧轮相同 | 部分问答仍错，p4 有新增无关人物干扰 |

本次三项 Search 均使用同一冻结候选 `9c7cf1b`，数据库来自多版本真实历史前缀，属于恢复后的检索质量评测，不是统一版本从头写入的整体成绩。此前各样本分数来自不同运行，不能把差值全部归因于代码。圈定的 972 题仍没有完整得分。

本轮完成了三个主要写入修补：将模型已选中的跨范围删除目标拆为分别核验的操作；暂缓发布与历史删除边界冲突的本次可选聚合卡，保留原始事实、消息和旧记忆；拒绝把助手的句子式开场误识别为人名。来源引用修补和来源核验的类型契约也已纳入统一候选。统一基底为 `5ca2929d09e718c4aa5353460a1f9338ad0a9c30`，构建与 678 项测试通过，尚有下述已知问题。

| 真实单点验证 | add 耗时 | 结果 |
|---|---:|---|
| B30，第 30 块 | 58.79 秒 | 成功，revision 30→31；两个目标分别通过语义核验 |
| B03，第 34 块 | 4.48 秒 | 成功，revision 33→34；记录聚合卡暂缓标记 |
| B03，第 45 块，旧单点 01 | 65.43 秒 | 失败；8 条修补后事实通过核验，最后的来源核验输出格式无效，revision 44 未变 |
| B03，第 45 块，新单点 02 | 22.61 秒 | 成功；7 条事实和 5 项来源判定通过标准核验，revision 44→45 |

B30 后续 20 次 add 全部成功，合计 244.29 秒，平均 12.21 秒。完整尾部评测约 405.65 秒；5 次 search 合计约 0.68 秒，其余约 160.63 秒包含回答、判分和评测开销，现有记录不能再准确拆成独立 Answer/Judge 耗时。

在原 B30 的 1/5 轮中，两个称呼问题需要 Patricia 的新状态。相关事实、原句和来源段落仍可见，但未进入实际返回的 32 条证据；这不是删除修补把新状态删掉。时间题需要的 December 证据也仍存在，但未返回，回答随后把另一人的 November 信息推给 Marc。遗忘对象题则缺少可公开检索的对象身份记录，需单独处理身份追溯与已删细节之间的边界。

B03 新单点在统一基底上将 `verificationResponseFormat` 设为 `json_object`，仍对完整输出执行严格本地结构和语义校验，没有截取 JSON 前缀或绕过核验。实际配置和两次语义阶段审计均确认该模式。原 44 条回执不变，第 45 条精确落库；降级标记仅为本次可选聚合卡暂缓。新旧模型抽取分别产生 7 条、8 条事实，来源候选也不同，因此一次成功不能证明格式故障已全面消失，耗时变化也不能全部归因于配置。官方文档提供该 JSON 模式及本地校验示例：[结构化输出](https://docs.bigmodel.cn/cn/guide/capabilities/struct-output)。

随后 B03 尾部 02 复用了前 45 块真实回执，完成最后 5 次新 add 和 6 道原题。全部判分完成且没有服务错误，最终 revision 50、50 条精确回执及全部进程退出均已核验。标准评测耗时 179.35 秒，其中 5 次新 add 合计 53.73 秒、平均 10.75 秒，6 次 search 合计 0.86 秒；其余约 125 秒包括回答、判分和评测开销。旧尾部 01 的 0/6 service_error 和旧单点 01 的失败均作为独立历史结果保留。

检索诊断使用录制的原始查询向量，精确复现了 B30 与 A06 共 10 次 Search。第一人称词触发的主体降权会连同相关第三方人物事实一起惩罚，来源证据还会再次降权。直接放宽非当前状态查询虽恢复了 B30 的部分关键证据，并保留 A06 唯一的预订证据，却给 A06 另两题引入了第三方干扰，因此没有将该策略纳入候选。

原 B03 的 4/6 轮中，两道错题也指向检索选取：目标成员入职及职级、高管职级、临时组长身份、汇报链事实均仍为 active、confirmed，对应完整来源仍可用。p1 返回 32 条证据，其中 26 条属于 user、6 条为操作事件；p5 返回的 32 条全部属于 user。目标人物事实与完整来源被降权或因数量限制未选中，并非写入抽取遗漏或被遗忘操作删掉。记录中的融合候选名次与后续 pack 名次不同，不能直接用前者判断最终覆盖。

查询范围候选 `9c7cf1b` 已实现并冻结，692 项测试及构建通过。它只根据原查询判断属性属于用户、其他人物或无法确定；仅明确指向其他人物时取消实际人物事实的主体惩罚，原始来源和操作事件的权重、可见性规则保持。功能默认关闭，验证时显式启用；分类与向量计算并行，每次最多一次请求、512 个输出 token、8 秒上限，失败走原路径，有界缓存只保留有效结果。

一次 34 个查询的真实分类验证已完成，平均耗时 1.42 秒、最长约 3.03 秒，全部单次成功。18 个预先固定的独立对照中 17 个分类符合预期；唯一偏差是 unknown→self_state，两者均保留原检索路径，没有将用户自身或模糊范围错误扩大为其他人物。其余 16 个为原评测查询，模型只收到查询文本。

用实际分类结果和原向量进行配对检索后，B03 p1/p5、B30 p3/p5 的关键主张和完整来源得到补充，B30 p4 的原正确依据保留。A06 p1/p2/p3/p5 的完整结果逐项不变，p3 的唯一预订事实、两条原引用及第 26 条的位置都保留。B30 p1 被实际模型分类为 self_state，原遗漏仍在；A06 p4 保留了朋友相关依据，却新增无关配偶、宠物证据。

`priority-query-focus-qa-20260909-01` 于上海时间 05:57:23 启动、06:05:45 完成：固定上述 `9c7cf1b` 候选，152 次完整历史幂等回执、16 道原题，顺序为 B03、B30、A06，单 worker。新 Engine 正常运行分类、检索、回答和判分，没有用此前 34 次分类结果填充缓存。实际 16 次查询范围调用全部成功；完整回执、三份数据库全表不变及执行完整性通过，写入生成与写入 embedding 调用均为零。

本次标准评测实际耗时 497.35 秒（约 8 分 17 秒）。16 次 Search 合计 30.38 秒、平均 1.90 秒；其中查询范围分类平均 1.79 秒、最长 3.27 秒，分类与 embedding 并行。其余约 466.77 秒包含回答、判分和评测开销，不能据此分别估算 Answer 与 Judge 耗时。152 次 add 仅复用历史回执，合计约 0.20 秒，不能作为新写入性能指标。

B03 的 p1/p5 与 B30 的 p3/p5 在此次转对，但 B03 p3 转错。只读核验表明，p3 的新旧查询向量完全相同，返回内容与事先配对结果一致；管理关系及完整职称来源仍在，还新增了独立职称事实。新回答确认了同一人与管理链，却漏写职称；两次 Judge 均按同一必含项判定。一次结果不能区分上下文排序、组合与生成波动的影响。

另一个隔离候选 `d44a5bbe` 修补了分类器不可用时的删除降级：存在未核验的歧义事实时拒绝整次提交，避免仅凭共享来源或缺少值引用就删除独立邻居。689 项测试通过，本地对照验证拒绝后全表不变。这个缺口未触发最近 B14 的实际失败，因此它不代表 B14 已恢复；尚不适合再次启动 972 题全量。

两个修补已无冲突合成后续候选 `8717d918`，703 项测试及构建通过，144 个产物两份哈希一致。这次 16 题仍归属 `9c7cf1b`；集成候选尚未进行新的真实评测或部署。

下一步转向原圈定的 LoCoMo `conv-43`：保留完整 29 个会话、680 条消息、43 次原分块 add 和 47 道原题，其中 24 道时间题、23 道多跳题。沿用 LoCoMo 原复现判分协议，而非 MemOps rubric。当前尚未启动该运行；它用于扩大未覆盖行为的验证，不再仅围绕这 16 题调参。

最新记录：[16 题实际评测结果](/Users/enoch/Workspace/comp/agent-memory-workspace/reports/priority-query-focus-qa-results-20260909.json)、[B03 p3 回归归因](/Users/enoch/Workspace/comp/agent-memory-workspace/reports/priority-query-focus-B03-p3-regression-20260909.json)、[下一轮覆盖与协议](/Users/enoch/Workspace/comp/agent-memory-workspace/reports/priority-next-coverage-20260909.json)。

详细记录：[查询范围真实验证](/Users/enoch/Workspace/comp/agent-memory-workspace/reports/priority-query-focus-diagnostic-20260909.json)、[16 题配对审查](/Users/enoch/Workspace/comp/agent-memory-workspace/reports/priority-query-focus-paired-review-20260909.json)、[降级删除修补](/Users/enoch/Workspace/comp/agent-memory-workspace/reports/priority-erasure-fallback-guard-20260909.json)、[B03 最新评测](/Users/enoch/Workspace/comp/agent-memory-workspace/reports/priority-B03-tail02-qa-results-20260909.json)、[B03 两题归因](/Users/enoch/Workspace/comp/agent-memory-workspace/reports/priority-B03-tail02-error-attribution-20260909.json)、[B30 完整评测](/Users/enoch/Workspace/comp/agent-memory-workspace/reports/priority-B30-tail-qa-results-20260909.json)、[检索诊断](/Users/enoch/Workspace/comp/agent-memory-workspace/reports/priority-B30-A06-packing-diagnostic-20260909.json)、[B03 第 45 块成功验证](/Users/enoch/Workspace/comp/agent-memory-workspace/reports/priority-B03-segment45-json-object-results-20260909.json)、[格式配置依据](/Users/enoch/Workspace/comp/agent-memory-workspace/reports/priority-source-format-recovery-design-20260909.json)、[统一修复基底](/Users/enoch/Workspace/comp/agent-memory-workspace/reports/priority-unified-repair-baseline-20260909.json)。
