# 部署与离线交付

本机已验证 Node 24.18.0、macOS arm64 / Docker Linux arm64。源码支持重新构建；当前导出的镜像为 arm64，未将 amd64 标为已验证平台。

## 镜像离线启动

交付包中的 `runtime-images.tar` 包含 service 与 eval 镜像及共同基础层。导入后在递归克隆的总控仓库执行：

```sh
docker load -i /path/to/runtime-images.tar
MEMORY_MODE=offline docker compose up -d --no-build --wait --wait-timeout 90
curl -f http://127.0.0.1:8088/health
```

必须等 `--wait` 成功再运行 `make contract` 或评测。`docker compose down` 保留具名数据卷；不要使用 `down -v` 作为正常停止命令。离线服务不调用外部模型，使用规则提取、来源证据和词法检索，其质量范围与增强模式不同。

镜像构建仍需预先准备基础镜像和依赖；“运行时离线”不表示可以在没有任何镜像、包缓存和模型的空机器上断网构建。

## 本地 embedding

当前已部署 Ollama `nomic-embed-text:latest`，维度 768，模型 manifest digest：

`0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f`

`local-embedding.tar` 只打包该模型 manifest 和所引用的内容寻址 blob，逐个 hash 已核验。Ollama 程序需预装。可以导入专用模型目录，使用独立端口，避免与现有模型服务冲突：

```sh
mkdir -p .models
tar -xf /path/to/local-embedding.tar -C .models
OLLAMA_MODELS="$PWD/.models" OLLAMA_HOST=127.0.0.1:11435 ollama serve
```

本机独立冷启动时遇到过 Metal 着色器编译等待：模型列表正常，但推理子进程在加载模型之前超时。已用相同归档在第二个新目录验证 CPU 回退，三条768维向量成功生成，相关文本相似度高于无关文本。对本机 Ollama 0.31.2，可以只在新实例启动命令中加入：

```sh
GGML_METAL_DEVICES=0 OLLAMA_MODELS="$PWD/.models" \
  OLLAMA_HOST=127.0.0.1:11435 ollama serve
```

这是实测的版本相关回退开关，不保证其它 Ollama 版本具有同样行为；[上游 Metal 后端源码](https://github.com/ggml-org/llama.cpp/blob/master/ggml/src/ggml-metal/ggml-metal.cpp)说明了该环境变量的读取方式。无需修改全局系统环境或重启已有正常模型服务。失败采样和完整回归分别见 `reports/embedding-startup-diagnostic.json`、`reports/embedding-import-verification.json`。CPU 回退用于新部署验证，本次公开评测的模型配置没有随之改变。

宿主机服务将 `MEMORY_EMBEDDING_BASE_URL` 指向 `http://127.0.0.1:11435`。Docker Desktop 场景使用 `DOCKER_EMBEDDING_BASE_URL=http://host.docker.internal:11435`；Linux 上需按实际网络配置可达地址。已有本机模型服务使用 11434，不必再起第二个。

增强模式在本地 `.env` 设置 `MEMORY_LLM_BASE_URL`、`MEMORY_LLM_API_KEY` 和明确的 `MEMORY_LLM_MODEL`；配置文件不要加入 Git。本文与交付包均不包含真实密钥。服务不会在请求过程中自动下载模型。向量空间不匹配会拒绝混写；更换模型后应创建新的数据目录并重新灌入。

启用完整候选的重排时，在 `.env` 中设置 `MEMORY_RERANK=true`，再执行：

```sh
MEMORY_MODE=enhanced docker compose up -d --no-build --wait --wait-timeout 90
```

## 评测及三仓库源码

三个 bare Git 仓库应保留在同一 `repositories/` 目录，然后：

```sh
git -c protocol.file.allow=always clone --recurse-submodules /path/to/repositories/agent-memory-workspace.git workspace
cd workspace
nvm use
make init test baseline-init eval-init
```

只有本地 Git bundle 使用上述一次性的 file 协议开关；无需修改全局 Git 配置。`make data` 从固定版本准备公开集并校验来源文件，联网数据准备与服务运行分离。

LoCoMo 上游复现另外需要已安装的 Qwen3:14b Q4_K_M 和 eval 的 `ollama-judge-server.py`。其 manifest digest 已保存于 `reports/holdout-v2-local-models.json`；9GB 级 Judge 权重没有混入 embedding 包。服务本身不依赖 Judge。正式平台提供资源后，应按正式模型与评分配置另建运行目录。

已验证的冷启动、断网恢复、干净克隆和 4/4 合成端到端结果见 `reports/clean-clone-final-functional.json` 与 `reports/container-offline.json`。补充许可与署名后的评测镜像运行文件等价记录见`reports/license-image-supplement.json`。完整公共基准结果与诊断仍由独立实验目录记录。

## 最终归档

完整评测、后处理、双阶段性能与逐项验收结束后，提交文档并用`make bundle`生成最新三仓库快照，再执行：

```sh
python3 scripts/package-delivery.py --snapshot delivery/<最新快照目录> --plan
python3 scripts/package-delivery.py --snapshot delivery/<最新快照目录>
```

`--plan`只列清单，不代表交付完成。正式打包要求所有完成门槛、干净Git状态和快照版本一致，检查所选文件与Git可达历史是否包含配置中的真实密钥，并在打包后逐文件读回验证SHA256。包内保留历史失败结果，排除真实环境文件、运行数据库、依赖缓存和重复模型导入目录。外部`.sha256`用于验证压缩包本身；`MANIFEST.json`用于验证包内每个源文件。
