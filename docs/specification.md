# Agent Memory 赛题规范（开源适配清单）

## 1. 赛题概述

实现一个 HTTP 记忆服务，仅暴露以下 3 个接口：

- `POST /add`
- `POST /search`
- `GET /health`

参赛系统只负责决定“记什么、怎么存、怎么召回”，不负责生成最终答案或判分。评测平台会将 `/search` 返回的记忆证据交给固定的 Answer 模型生成答案，再由 Judge 判分。

## 2. 接口契约

接口契约属于硬性门槛。候选项目即使采用 SDK 或框架调用方式，也必须能够通过轻量适配层封装为以下 REST 接口。

### 2.1 添加记忆

`POST /add`

#### 请求字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `request_id` | string | 是 | 本次请求的唯一标识 |
| `user_id` | string | 是 | 用户标识，作为记忆隔离边界 |
| `session_id` | string | 是 | 会话标识 |
| `messages` | array | 是 | 对话消息列表 |
| `messages[].role` | string | 是 | 消息角色，如 `user`、`assistant` |
| `messages[].content` | string | 是 | 消息正文 |
| `messages[].timestamp` | string | 是 | 消息时间戳 |

请求示例：

```json
{
  "request_id": "req-001",
  "user_id": "user-001",
  "session_id": "session-001",
  "messages": [
    {
      "role": "user",
      "content": "我下个月要搬到杭州。",
      "timestamp": "2026-09-05T09:00:00+08:00"
    },
    {
      "role": "assistant",
      "content": "好的，祝你搬家顺利。",
      "timestamp": "2026-09-05T09:00:10+08:00"
    }
  ]
}
```

#### 成功响应

```json
{
  "success": true,
  "request_id": "req-001",
  "user_id": "user-001",
  "session_id": "session-001"
}
```

#### 关键约束

- 必须同步写入。
- 返回 HTTP `200` 后，新写入的记忆必须能立即被 `/search` 检索。
- 禁止返回 HTTP `202` 或依赖异步写入完成核心存储与索引流程。
- `request_id`、`user_id`、`session_id` 必须在响应中原样回显。

### 2.2 搜索记忆

`POST /search`

#### 请求字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `query` | string | 是 | 检索问题 |
| `user_id` | string | 是 | 用户标识，只能检索该用户的记忆 |
| `top_k` | number | 是 | 最大返回数量，评测值为 `100` |
| `options` | array | 否 | 可选项列表，可用于候选答案相关的检索增强 |

请求示例：

```json
{
  "query": "用户准备搬到哪个城市？",
  "user_id": "user-001",
  "top_k": 100,
  "options": ["上海", "杭州", "深圳"]
}
```

#### 成功响应

```json
{
  "data": [
    {
      "id": "memory-001",
      "content": "用户计划在下个月搬到杭州。",
      "score": 0.87,
      "created_at": "2026-09-05T09:00:00+08:00"
    }
  ]
}
```

无结果时返回：

```json
{
  "data": []
}
```

#### 关键约束

- 只返回记忆证据，不得生成最终答案。
- 结果必须按相关性排序。
- 返回数量不得超过 `top_k`，且最多为 100 条。
- 必须按照 `user_id` 严格隔离检索结果。

### 2.3 健康检查

`GET /health`

| 项目 | 要求 |
| --- | --- |
| 请求参数 | 无 |
| 成功响应 | 任意 HTTP `2xx` |
| 鉴权 | 无 |

## 3. 数据灌入格式

### 3.1 数据组织

- 一个 sample 对应一个 `user_id` 和 N 个 session。
- 每个 session 包含一个 `messages[]` 数组。
- 消息通常由 `user` 和 `assistant` 交替产生，并带有 `timestamp`。
- 评测机按 chunk 调用 `/add`：超过约 20 条消息或约 2,000 词时会切块。
- LoCoMo 会在会话首条消息中注入 `[Session time: ...]`，作为时序锚点。

### 3.2 样例数据字段

```text
sample_id
isolation.user_id
add_phase.sessions[]
search_items[]
  ├── qid
  ├── question
  ├── gold_answer
  ├── gold_rubric
  └── options
```

## 4. 核心能力要求

| 能力 | 要求 | 主要评测基准 |
| --- | --- | --- |
| Extract（提取） | 从对话中识别值得长期记忆的事实，并过滤闲聊 | LoCoMo、MemOps |
| Store（存储） | 对记忆进行结构化存储和索引，支持高效检索 | LoCoMo、MemOps |
| Recall（召回） | 跨会话按 `user_id` 检索，支持 `top_k=100` | LoCoMo 为主 |
| Update（更新） | 用户更新偏好或事实后，使旧值失效、新值生效 | MemOps 为主 |
| Forget（遗忘） | 收到遗忘指令后，目标记忆不再被优先返回，同时避免误删无关记忆 | MemOps |
| Anti-noise（抗噪） | 避免将闲聊或临时信息写入长期记忆，并避免误召回已有噪声 | MemOps |

## 5. 评测基准

| 基准 | 仓库 | 许可 | 评测重点 |
| --- | --- | --- | --- |
| LoCoMo-Refined | [mem-eval-suite/LoCoMo_refined](https://github.com/mem-eval-suite/LoCoMo_refined) | CC-BY-NC-4.0 | 长对话、跨会话、单跳、多跳、时序；包含开源 Judge：`src/llm_judge.py` |
| MemOps | [MemTensor/MemOps](https://github.com/MemTensor/MemOps) | MIT | 记忆生命周期 Remember、Update、Forget、Reflect，以及纵向噪声会话 |

适配与实现前应优先检查这两个上游仓库，其中可能包含可复用的 baseline、数据生成器和 Judge 脚本。

## 6. 评测流程与计分

### 6.1 单个 sample 的评测流程

```text
for each chunk:
  POST /add

for each question:
  POST /search(query, user_id, top_k=100)
  -> Answer(memories)
  -> Judge(answer, gold)
  -> 0 or 1
```

### 6.2 计分规则

- 客观分为正确题数除以 1,000。
- LoCoMo 共 500 题，MemOps 共 500 题，两者等权。
- LoCoMo 使用 Qwen3-14B 和开源 refined Judge，输出 `CORRECT` 或 `WRONG`。
- MemOps 按 `gold_rubric` 规则判分，例如：

  ```json
  {
    "must_include": ["Portland"],
    "must_not_include": ["Seattle"]
  }
  ```

- 不设部分分，每题只能得 0 分或 1 分。

## 7. 技术约束

- 使用 Docker 或等价方式部署，并能在干净环境中冷启动。
- 单次 `/add` 请求超时范围为 1–120 秒。
- 单次 `/search` 请求超时范围为 1–60 秒。
- 必须按 `user_id` 严格隔离，禁止不同样本之间串记忆。
- 可以使用 LLM 进行记忆抽取或生成 embedding。
- 正式评测可用的模型资源以赛题组通知为准，因此必须提供本地或离线回退方案。

## 8. 开源项目调研方向

以下内容是项目筛选方向，不属于接口硬性规范。

### 8.1 Agent 记忆系统

推荐搜索词：

- `agent memory`
- `LLM long-term memory`
- `memory service for LLM`
- `agent memory layer`

重点候选：

| 项目 | 关注点 |
| --- | --- |
| [mem0](https://github.com/mem0ai/mem0) | 具备 Add/Search 语义，支持向量库、LLM 抽取、更新与遗忘，功能覆盖度较高 |
| [Letta / MemGPT](https://github.com/letta-ai/letta) | 自包含 Agent 记忆系统，提供记忆块管理 |
| [Zep](https://github.com/getzep/zep) | 偏生产级的长期记忆服务 |
| [Cognee](https://github.com/topoteretes/cognee) | 知识图谱式记忆 |

### 8.2 RAG 框架

- LangChain Memory、LlamaIndex Memory：提供对话记忆抽象，但需要自行补充 Update 和 Forget 能力。
- Haystack：管线式框架，可改造成 Add/Search 服务。

### 8.3 检索组件

- 向量数据库或索引：Chroma、Qdrant、FAISS、LanceDB。
- 混合检索：BM25 与向量检索组合，用于增强精确实体匹配和旧值过滤。

### 8.4 评测与基准复用

优先研究 LoCoMo-Refined 和 MemOps 上游仓库中的 baseline 实现、数据生成器与 Judge 脚本。

## 9. 实现验收清单

### 接口与一致性

- [ ] 仅暴露 `POST /add`、`POST /search`、`GET /health`
- [ ] `/add` 成功后记忆立即可检索
- [ ] `/add` 原样回显三个 ID
- [ ] `/search` 只返回证据，不生成答案
- [ ] `/search` 无结果时返回 `{"data":[]}`
- [ ] `/health` 无需鉴权并返回 `2xx`

### 记忆能力

- [ ] 能从对话中提取长期事实并过滤闲聊
- [ ] 支持跨 session 召回
- [ ] 支持事实与偏好更新
- [ ] 支持遗忘且不误删无关记忆
- [ ] 能处理临时信息和噪声会话
- [ ] 能利用 Session time 和消息时间戳处理时序问题

### 隔离、部署与性能

- [ ] `user_id` 在存储和检索层均严格隔离
- [ ] Docker 或等价环境可冷启动
- [ ] `/add` 在规定超时内完成
- [ ] `/search` 在规定超时内完成
- [ ] 外部模型不可用时有本地或离线回退方案

## 10. 项目工程要求（重新实施补充）

本节是项目自主制定的工程要求，不属于赛题组原始规范。重新设计和实施时，应采用“两个功能仓库 + 一个总控仓库”的组织方式；原有赛题接口与计分规则保持不变。

### 10.1 仓库职责

| 仓库 | 职责 | 必须包含 |
| --- | --- | --- |
| `agent-memory-service` | 记忆服务及服务端接口实现 | 三个 HTTP 接口、记忆抽取与存储、更新与遗忘、检索、模型适配、内部测试、独立部署文件 |
| `agent-memory-eval` | HTTP 客户端封装及评测系统 | 请求与响应校验、LoCoMo/MemOps 数据适配、分块灌入、搜索调用、Answer/Judge 接入、合约测试、指标与报告 |
| `agent-memory-workspace` | 总控、版本固定和集成编排 | 两个功能仓库的 Git submodule、统一配置示例、启动与评测命令、集成测试、实验版本清单 |

这里的“接口封装”指评测端使用的 HTTP 客户端。将记忆引擎适配成 `/add`、`/search`、`/health` 的服务端封装，必须归属 `agent-memory-service`，保证提交物可独立启动。

### 10.2 依赖与隔离要求

- 三个仓库必须分别具有独立的 Git 历史，不能仅以同一仓库下的三个普通目录代替。
- `service` 独立构建、测试和部署，不依赖 `eval` 或总控仓库的运行时代码。
- `eval` 仅通过 HTTP 调用被测服务，不导入其内部模块，也不读取其数据库或索引。
- `eval` 通过可配置的 `base_url` 测试不同实现，保持数据灌入和计分流程一致。
- 总控仓库负责组合和编排，不承载记忆算法或评测业务逻辑。
- gold answer、rubric 和 gold evidence 仅供评测端计分与诊断使用，不得传入服务；`/search` 只发送赛题允许的字段。

### 10.3 总控仓库结构

```text
agent-memory-workspace/
├── service/                 # Git submodule: agent-memory-service
├── eval/                    # Git submodule: agent-memory-eval
├── compose.yaml
├── Makefile                 # 或等价的统一命令入口
├── configs/                 # 配置示例，不包含密钥
├── scripts/                 # 初始化、启动、测试和评测脚本
├── experiments/             # 实验清单及可追溯的结果摘要
└── README.md
```

- submodule 固定到精确 commit，不依赖浮动分支的最新状态。
- 从递归克隆的总控仓库即可完成初始化、构建、启动、测试和评测；脚本不能依赖仓库之外的兄弟目录或开发者绝对路径。
- 提供统一命令执行：依赖初始化、构建、启动、合约测试、小规模端到端测试、LoCoMo 评测、MemOps 评测、报告生成和停止服务。
- 正常停止服务应保留持久数据；清理测试数据应使用单独且范围明确的命令。

### 10.4 契约与版本管理

- 在 `eval/contracts/` 维护权威静态 OpenAPI 或 JSON Schema，严格对应赛题的三个接口。
- `service` 声明兼容的契约版本，并通过该版本的黑盒合约测试；复制契约时须校验版本和内容一致。
- 静态契约文件不意味着增加对外接口，运行时仍只暴露赛题规定的三个路径。
- 每次实验记录 service、eval、workspace 的 commit，以及数据版本/校验和、样本划分、模型与 prompt 版本、运行参数和计分方式。
- 召回覆盖率、启发式代理分与正式 Answer/Judge 得分分别报告；只有实际运行正式配置后，才可报告对应的正式得分。

### 10.5 技术栈要求（TypeScript 优先）

本项目提供的赛题规范没有限定实现语言，允许使用 TypeScript（TS）。重新实施时，优先采用 TypeScript 技术栈，不沿用此前 Python 原型作为默认选型。

| 仓库 | 技术栈要求 |
| --- | --- |
| `agent-memory-service` | 优先使用 TypeScript + Node.js，实现 HTTP 接口、记忆处理、存储和检索；开启 TypeScript 严格类型检查 |
| `agent-memory-eval` | HTTP 客户端封装和评测编排优先使用 TypeScript；需要复用 LoCoMo/MemOps 官方 Python 脚本时，可通过独立进程或容器调用，也允许评测仓库采用 Python，须在设计中明确理由 |
| `agent-memory-workspace` | 使用 Docker Compose 和统一命令入口编排；辅助脚本可使用 TypeScript 或 Shell |

具体框架、数据库驱动、测试工具和 Node.js 版本在重新设计时确定，并固定依赖版本和锁文件。选择 TS 不改变以下要求：

- `/add` 必须等待核心存储与索引可查询后才返回成功；使用 `async/await` 不等于允许后台异步完成写入。
- HTTP 输入和模型输出必须进行运行时校验，不能仅依赖编译期类型。
- 存储、检索与缓存均按 `user_id` 隔离，且只暴露赛题规定的三个接口。
- 模型调用通过可替换的适配层接入；外部模型不可用时仍有本地或离线回退方案。
- 官方 Judge 的逻辑、提示词和计分口径应保持一致；调用 Python 脚本时固定其版本与依赖，并由统一命令编排。
- 提供类型检查、自动化测试、构建和 Docker 冷启动验证；CPU 密集型检索应验证对 Node.js 请求处理与超时的影响。

三仓库通过 HTTP、静态契约和实验文件协作，不要求共享同一运行时，也不要求为了语言统一而重写官方评测脚本。

### 10.6 工程验收清单

- [ ] 两个功能仓库及一个总控仓库均为独立 Git 仓库
- [ ] 总控仓库以 submodule 固定功能仓库版本
- [ ] 服务可脱离总控仓库独立构建和启动
- [ ] 评测器仅通过 HTTP 访问服务，支持切换 `base_url`
- [ ] 契约版本固定，服务与客户端通过同一版本的合约测试
- [ ] 从干净环境递归克隆后可通过统一命令完成端到端测试
- [ ] LoCoMo 和 MemOps 均有数据适配、评测入口和结果报告
- [ ] 实验结果能追溯到代码、数据、模型、prompt 和配置版本
- [ ] README 说明各仓库职责、初始化方式、运行命令和交付方式
- [ ] 设计明确 TS 优先的技术栈选择、运行时版本及必要的 Python 依赖边界
- [ ] TS 项目通过严格类型检查、运行时契约校验、自动化测试与构建验证
