# Agent Memory Service（可独立部署记忆服务）

面向赛题三接口的记忆服务：**ADD**（自主提取，同步可检）、**SEARCH**（只返证据）、**HEALTH**（就绪探测）。同一镜像两种形态：`offline`（零外部依赖，规则提取 + 词法检索）与 `enhanced`（LLM 分组提取 + 语义检索，任一模型能力故障自动降档为确定性方案并带审计标记提交）。内容型 add 附带确定性跨会话聚合（pattern 卡，服务列表完备与 Reflect 汇总）与多源佐证排序信号，两形态行为一致。

设计说明见 [docs/方案设计-最终交付版.md](docs/方案设计-最终交付版.md)（赛题对齐、六能力机制矩阵、写入成功哲学）。工作区结构：本仓负责编排与报告，`service/`（TS 服务实现）与 `eval/`（HTTP 契约校验器/评测器）为固定 commit 的 submodule，只通过 HTTP 通信。

## 快速启动（非交互）

```sh
make init && make build
make up-offline          # docker compose，默认 configs/release-offline.env，零外部依赖
make contract            # 12 项契约校验（http://127.0.0.1:8088）
make down
```

- **Docker**：`docker compose up -d` 默认 offline 形态；LLM 形态用 `MEMORY_CONFIG_FILE=configs/release-enhanced.env docker compose up -d`，并在本地 `.env` 填 `MEMORY_LLM_API_KEY` / `MEMORY_LLM_BASE_URL`。`stop_grace_period: 125s` 覆盖 add 尾时。
- **裸机**：`set -a; . configs/release-offline.env; set +a; MEMORY_DATA_DIR=/data/mem node service/dist/server.js`（:8088）。
- 增强模式的本地 embedding（nomic-embed-text）须预置于 Ollama，服务不自动下载。

## LLM 使用披露（赛题要求）

`offline` 形态不调用任何模型。`enhanced` 形态（`configs/release-enhanced.env`，OpenAI/Ollama 兼容端点）使用：

| 用途 | 模型（配置项） | 失败行为 |
|---|---|---|
| 分组提取与修补 | `gpt-5.4-mini` 起步档，提取/核验/修补各可独立指定（`MEMORY_EXTRACTION_MODEL` 等当前为 `gpt-5.5`） | 能力故障（网络/超时/坏 JSON/宕机）→ 降档为确定性离线提取，`extraction_offline` 审计标记，HTTP 200 保持可检索 |
| 证据核验 | 同上 `gpt-5.5` | 协议错一轮修补；核验通道不可用 → 降档离线方案（离线事实以逐字引证自证，不经语义核验）；**模型健康但语义认证"不确定/不独立" → 保持拒绝，不降级** |
| 擦除独立性 / 源擦除 / 状态转移 | 同上 `gpt-5.5` | 能力故障 → 确定性方案（全候选擦除 / 全标 uncertain）+ 审计标记 |
| 嵌入 | `nomic-embed-text`（本地 Ollama） | 不可用 → 词法路由降级（`embedding_lexical`） |
| 兜底与 rerank 位 | `gpt-5.4-mini`（`MEMORY_LLM_MODEL`） | rerank 关闭时仅作未映射用途兜底 |

评测机不重放失败的 add，故一切**能力故障**降档提交而非 5xx；**语义拒绝**（退休指令不可绑定、证据不成立）保持 fail-closed——HTTP 200 必须意味着指令生效。写续传模式（`MEMORY_WRITE_CONTINUATION`，交互式客户端用）保持基座严格契约，**两份 release 配置均已关闭**。

## 接口契约

- `POST /add` `{request_id, user_id, session_id, messages:[{role, content, timestamp?}]}` → 200 `{success, request_id, user_id, session_id}`。同 `request_id` 重放返回回执；同 ID 异载荷 409。无 `timestamp` 的消息获得严格递增的合成排序标记（`time_basis='ordering'`），该标记不充当日期锚。
- `POST /search` `{query, user_id, top_k}`（可带 `options`）→ 200 `{data:[{id, content, score, created_at}]}`，只返记忆证据，不生成答案。选择题候选嵌在题面时走四级匹配梯（精确等值 → 数字守卫 → 短串守卫 → 长文重叠），唯一精确命中强制 rank-1。
- `GET /health` → 引擎就绪即 `200 {"status":"ok","models":"ok"|"degraded"}`；模型探测（GET /models，30s 缓存）只作元数据，不阻断就绪（降档提交使死端点下服务仍可用）。
- `user_id` 为检索隔离键：每 user 独立 SQLite 目录 + 串行写队列；`add ≤ 115s`、`search ≤ 55s` 内部预算（低于赛题 120s/60s）。

## 配置矩阵

| 配置 | 形态 | 提取 | 检索 | 说明 |
|---|---|---|---|---|
| `configs/release-offline.env` | offline | 确定性规则 | 词法 FTS5（porter 词干）+ 原文 | 零依赖，评测兜底形态 |
| `configs/release-enhanced.env` | enhanced | LLM 分组（v5 组合） | 混合（语义+词法+实体）+ 原文 | source-first v10 家族（routing/batches/source-first）保持关闭：其"独立覆盖"表示依赖活模型、无法降档，可用性优先 |

## 验证状态（2026-09-07）

- 单元 **431/431**（`cd service && npm test`；含并发、故障注入、原子回滚、Unicode、聚合卡 7 项、多源佐证 2 项）。
- 契约校验器 **12/12**（offline 实例）。
- intent v2 全量回归：LoCoMo refined 1,382 题 {CURRENT:1376, HISTORICAL:5, TRAJECTORY:1} 零误报；MemOps 纵向 134 去重对 32/32 命中 TRAJECTORY。
- 场景回归：无时间戳 add、现值/历史包裹模板、题面候选、时间 unresolved、遗忘全路径、**聚合卡端到端**（跨会话累积→单卡全值→forget 全路径清→干净重建）、**enhanced + 死 LLM 端点端到端**（health 2xx 如实 degraded、add 降档 200、检索命中且聚合卡照常产出）。
- 已知边界：offline 形态转述类查询召回有限（无嵌入），由 enhanced 形态覆盖；source-first v10 表示未纳入 release（见上表）。

## 历史与归档

本仓继承基座（EnochLi15/agent-memory-workspace）全部工程骨架（事务化存储、双源索引、幂等、写续传）与历史评测档案。基座 glm-5.2 两轮 100 题 0/100 写入失败的事故记录与限流修正见 [reports/](reports/)；其根因（source-first 严格契约 + 分组/核验覆盖失败即 5xx）已由本轮降档哲学修复并以测试固化。历史 GPT 331/1000 等归档仅作对照，不代表当前配置。部署细节见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)，模型准备见 [docs/MODEL-SETUP.md](docs/MODEL-SETUP.md)，实验协议见 [docs/EXPERIMENT-PROTOCOL.md](docs/EXPERIMENT-PROTOCOL.md)。
