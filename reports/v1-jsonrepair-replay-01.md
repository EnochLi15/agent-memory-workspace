# jsonrepair 离线回放

2026-09-07；包版本 `jsonrepair@3.15.0`，仅安装在忽略的 artifacts 目录。运行时服务依赖和代码未改动。

对已完成的 `v1-public-100-glm52-03` 私有模型日志中全部两条原始非法 JSON 回放。没有模型调用、数据库写入或评测重跑。

| 原始失败 | 包修复结果 | 现有校验结果 | 相对现有代码的新增恢复 |
| --- | --- | --- | --- |
| conv-50 session-12，抽取尾逗号 | 仅删除偏移 913 的一个逗号 | 来源引用解码通过，13 条事实；与现有 parseModelJson 结果深度相等，未做新的语义验证 | 无 |
| A06_reflect segment-11，验证结构损坏 | 可被 JSON.parse 解析，但把 message/verdict_note 字段变成数组里的五个字符串 | 现有 decodeCompactVerification 返回五个非法元组错误；第 8 条消息仍无有效判定 | 无 |

结论：这两例中，语法可解析为 2/2，协议及来源引用解码通过为 1/2（不代表写入成功），相对当前代码新增有效恢复为 0。暂不将宽松 jsonrepair 接入提交路径。它可以生成候选，但不能重建缺失判定，不能解除删除不确定性；这两例也不足以判断其他格式错误的收益。

复现：在工作区根目录，使用项目要求的 Node 版本运行 `node artifacts/v1-jsonrepair-replay-01/replay.mjs`。私有原始日志和本地安装仍须存在；没有创建新实验框架。

本地证据：`artifacts/v1-jsonrepair-replay-01/` 中的 replay.mjs、result.json、package-lock.json 和两份修复输出。原始日志 SHA-256：`f32e0fa929043766c284931e3e64663a9c2a622cd6c177bbc644cd4a8f558376`。各原始输出及修复输出哈希记录在 result.json 中。
