# Agent Memory 赛题交付工作区

三个独立仓库：本工作区负责规范、版本、编排及报告；`service/` 是 mem0 TS 源码改造服务；`eval/` 是只通过 HTTP 通信的独立评测器。两者是固定 commit 的 Git submodule，不能互相引用运行时代码。

## 快速运行

宿主机：Node 24.18.0、Docker。使用 `nvm use`，然后：

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

正式赛题 Answer、500+500 选择器与 MemOps 判分映射尚未提供。当前公开复现使用明确记录的本地转换与模型，不能当成正式平台得分。验证状态见 `docs/IMPLEMENTATION-STATUS.md`；已执行的工程和问答实验报告将放入 `reports/`。

离线 Git 交付：`make bundle` 创建三个 bare 镜像，并实际验证递归克隆。内部相对 submodule URL 适配三个镜像相邻的目录；上传到代码托管平台时按实际位置配置远端。
