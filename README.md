# Agent Memory 赛题交付工作区

三个独立仓库：本工作区负责规范、版本、编排及报告；`service/` 是 mem0 TS 源码改造服务；`eval/` 是只通过 HTTP 通信的独立评测器。两者是固定 commit 的 Git submodule，不能互相引用运行时代码。

当前唯一交付主线是 [V1交付收敛计划](docs/29-V1交付收敛计划.md)：修复交付阻塞、固定发布配置、通过小集闭环与原文对照、完成一次1000题评测，随后封版。当前仅计划已落盘，尚未完成新V1验收；实际结果见 [第二轮状态](docs/ROUND2-STATUS.md)。

## 快速运行

宿主机：Node 24.18.0、Python 3、Docker。使用 `nvm use`，然后：

```sh
make init
make build
MEMORY_MODE=offline make up
make contract
make down
```

离线模式镜像完成构建后不需要网络或模型；停止默认保留卷。增强模式参考 `service/.env.example` 在本地 `.env` 配置 LLM 与 embedding，凭据文件已被 Git 忽略。Ollama 的本地 embedding 模型必须预先准备，服务不会自动下载。

`make smoke` 额外调用已配置的 Answer/Judge，运行两类合成样本。公开数据先按 eval 文档下载和转换，然后使用 `make eval-locomo`、`make eval-memops`。`make report RUN_ID=...` 汇总；`make clean-run RUN_ID=...` 仅删除指定实验文件；`make down` 不删持久记忆。

`make eval-init data` 初始化固定 Python Judge 依赖并下载、校验和转换公开数据；`make baseline-init` 初始化 U0/U1 对照并验证重构等价。源码对照、B0–B6 和单项消融的配置与运行方式见 [实验协议](docs/EXPERIMENT-PROTOCOL.md)。MemOps 每个问题实例的 ID 包含 evaluation setting，避免合并成对探针。端到端代理得分和上游生命周期诊断分别保存。

正式赛题 Answer、500+500 选择器与 MemOps 判分映射尚未提供。当前公开复现使用明确记录的本地转换与模型，不能当成正式平台得分。完整实现、验证、评测与已知缺陷见[交付报告](reports/DELIVERY-REPORT.md)；模型列表、生成与本地向量验证见[模型配置](docs/MODEL-SETUP.md)。

离线 Git 交付：`make bundle` 创建三个 bare 镜像，并实际验证递归克隆。内部相对 submodule URL 适配三个镜像相邻的目录；上传到代码托管平台时按实际位置配置远端。

详细离线镜像、本地 embedding 导入和部署边界见 [部署说明](docs/DEPLOYMENT.md)。历史交付实例曾在 `http://127.0.0.1:8088` 通过增强模式 contract，配置与当时验证见 [部署验证](reports/deployed-service.json)；这不代表本次候选或该端口当前状态。历史1000题主评测、32组开发对照、完整诊断和两轮性能已有归档，本地配置结果331/1000，含36题服务错误。历史归档校验见`delivery/final-verification.json`，相应边界见[实施状态](docs/IMPLEMENTATION-STATUS.md)。

第二轮服务冻结`98c12e5`、eval`11da2a6`的1000题评测已经中断：LoCoMo 83/500，MemOps尚有348题无结果，不能报告新的完整准确率。原始现场与历史331/1000保留，监控已暂停。详见[中断归档](reports/round2-full-98c12e5-interrupted.md)；后续实施按[V1交付收敛计划](docs/29-V1交付收敛计划.md)推进。
