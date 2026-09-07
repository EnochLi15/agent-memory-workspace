# Agent Memory 赛题交付工作区

三个独立仓库：本工作区负责规范、版本、编排及报告；`service/` 是 mem0 TS 源码改造服务；`eval/` 是只通过 HTTP 通信的独立评测器。两者是固定 commit 的 Git submodule，不能互相引用运行时代码。

当前交付为候选版，尚未完成正式V1验收。最新接入 BigModel：当前按用户要求统一使用`glm-5.2`负责写入、核验、修复、回答与判分，本地nomic embedding保持不变。[此前GLM-5.3/Flash固定小集](reports/v1-small-bigmodel-02-results.json)中，候选5次写入、10次检索和20项存储检查通过；机器计分8/10（9题已判、1题判分JSON错误），原文5/10。助手复核不是人工校准，也不替换机器分数。

下一项必须关闭的是判分JSON错误；完整小集、长背景、1000题评测与正式封版仍按[V1交付收敛计划](docs/29-V1交付收敛计划.md)验收。最新评测范围缩减为100题（50 LoCoMo + 50 MemOps），数据及模型配置见[本轮冻结清单](configs/v1-public-100-glm52.json)；旧1000题计划不再执行。此前GPT配置的[小集](reports/v1-small-b398a63-results.md)、[长背景](reports/v1-long-b398a63-results.md)、部署和恢复证据独立保留，不能作为新模型配置已经通过的证据。

最新100题评测已结束：[结果报告](reports/v1-public-100-glm52-results.md)。100题全部因背景写入失败成为service_error，端到端0/100、无已判分答案；已记录429限流及分组/核验覆盖问题，未自动重跑。

限流修正：BigModel候选现采用服务内共享串行队列、请求启动间隔至少1秒、429共享等待；评测固定单背景并发，LoCoMo结束后再启动MemOps。说明及本地验证见[限流修正记录](reports/v1-glm52-rate-limit-fix.md)。串行100题复测已完成：[最新结果](reports/v1-public-100-glm52-02-results.md)。本轮模型调用47次、429为0、峰值并发为1；但100题仍全部写入失败，端到端0%，无已判分答案。分组/核验覆盖和非法JSON问题仍未解决。

## 快速运行

宿主机：Node 24.18.0、Python 3、Docker。使用 `nvm use`，然后：

```sh
make init
make build
make up-offline
make contract
make down
```

离线模式镜像完成构建后不需要网络或模型；停止默认保留卷。增强模式使用 `configs/release-enhanced.env`，在本地 `.env` 配置 LLM 连接与凭据后执行 `make up`；离线模式有独立配置和数据卷，凭据文件已被 Git 忽略。Ollama 的本地 embedding 模型必须预先准备，服务不会自动下载。

BigModel候选使用 `configs/v1-bigmodel-enhanced.env`。在受Git忽略的本地`.env`中设置以下两项并自行填写`MEMORY_LLM_API_KEY`，再按部署说明启动：

```dotenv
MEMORY_CONFIG_FILE=configs/v1-bigmodel-enhanced.env
MEMORY_LLM_BASE_URL=https://open.bigmodel.cn/api/coding/paas/v4
```

该配置使用`json_object`及本地严格校验；协议连通不等于完整验收通过。详细限制和原始结果见[接入交接说明](docs/DEPLOYMENT.md)。

`make smoke` 额外调用已配置的 Answer/Judge，运行两类合成样本。公开数据先按 eval 文档下载和转换，然后使用 `make eval-locomo`、`make eval-memops`。`make report RUN_ID=...` 汇总；`make clean-run RUN_ID=...` 仅删除指定实验文件；`make down` 不删持久记忆。

`make eval-init data` 初始化固定 Python Judge 依赖并下载、校验和转换公开数据；`make baseline-init` 初始化 U0/U1 对照并验证重构等价。源码对照、B0–B6 和单项消融的配置与运行方式见 [实验协议](docs/EXPERIMENT-PROTOCOL.md)。MemOps 每个问题实例的 ID 包含 evaluation setting，避免合并成对探针。端到端代理得分和上游生命周期诊断分别保存。

正式赛题 Answer、500+500 选择器与 MemOps 判分映射尚未提供。当前公开复现使用明确记录的本地转换与模型，不能当成正式平台得分。历史实现、验证、评测与已知缺陷见[历史交付报告](reports/DELIVERY-REPORT.md)；模型列表、生成与本地向量验证见[模型配置](docs/MODEL-SETUP.md)。

离线 Git 交付：`make bundle` 创建三个 bare 镜像，并实际验证递归克隆。内部相对 submodule URL 适配三个镜像相邻的目录；上传到代码托管平台时按实际位置配置远端。

详细离线镜像、本地 embedding 导入和部署边界见 [部署说明](docs/DEPLOYMENT.md)。历史交付实例曾在 `http://127.0.0.1:8088` 通过增强模式 contract，配置与当时验证见 [部署验证](reports/deployed-service.json)；这不代表本次候选或该端口当前状态。历史1000题主评测、32组开发对照、完整诊断和两轮性能已有归档，本地配置结果331/1000，含36题服务错误。历史归档校验见`delivery/final-verification.json`，相应边界见[实施状态](docs/IMPLEMENTATION-STATUS.md)。

第二轮服务冻结`98c12e5`、eval`11da2a6`的1000题评测已经中断：LoCoMo 83/500，MemOps尚有348题无结果，不能报告新的完整准确率。原始现场与历史331/1000保留，监控已暂停。详见[中断归档](reports/round2-full-98c12e5-interrupted.md)；后续实施按[V1交付收敛计划](docs/29-V1交付收敛计划.md)推进。
