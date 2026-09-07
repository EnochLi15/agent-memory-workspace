# 无 Docker 本地运行与调试

支持 macOS / Linux 的原生进程流程。需要 Node 24.18.0（`nvm use`）、npm、Git、make。SQLite 随 `better-sqlite3` 在进程内运行，不需要单独数据库服务。`npm ci` 如需编译原生模块，还需要 Python 3 和系统 C/C++ 编译工具（macOS Command Line Tools，Linux build-essential）。Python Judge 只在相应评测中需要。

## 首次启动

```sh
git submodule update --init --recursive
nvm use
make init
make build
make local
```

`make build` 只编译 service 和 eval；容器镜像改用 `make docker-build`。`make local` 默认离线模式，在前台监听 `http://127.0.0.1:8088`，不需要 API key、Ollama 或 Docker。首次安装 npm 依赖仍需要网络或本地包缓存。

另开终端，在同一工作区执行：

```sh
nvm use
make contract
```

该 HTTP 闭环覆盖写入、检索、幂等、更新、遗忘和邻居保留，不调用 Answer/Judge，也不是准确率评测。普通日志直接输出在启动终端；Ctrl+C 优雅停止并保留数据，再执行 `make local` 可恢复访问。不要同时启动多个进程使用同一数据目录。

## 源码开发与断点

```sh
make local-dev
# 或启用 Node inspector：
make local-debug
```

两者都会先编译，包括 SQLite Worker，再启动服务。修改 `service/src/` 或 `service/tsconfig.json` 后，停止旧服务、重新编译并重启；编译失败则保持停止，修复代码后自动再试。重启会丢失内存中的未提交准备，已提交 SQLite 数据保留；有在途请求时优雅停止最多等待125秒。

调试模式在 `127.0.0.1:9229` 开放 inspector。VS Code 选择随仓库提供的 “Attach local memory service (make local-debug)” 后启动调试，断点设置在 `service/src/*.ts`；source map 映射至编译产物。可用 `LOCAL_INSPECT_PORT=9230 make local-debug` 换调试端口，同时修改 attach 配置的端口。数据库 Worker 会作为独立线程运行。

## 增强模式

本地增强入口固定复用 `configs/v1-bigmodel-enhanced.env`：远端 GLM-5.2，本机 Ollama nomic embedding。先准备并启动 Ollama，安装指定 embedding；下载的模型必须符合配置中的 digest，不能为通过启动而删除校验。

```sh
ollama serve
# 另一终端，尚未安装时执行：
ollama pull nomic-embed-text:latest
```

在根目录已有 `.env` 中填写连接信息，或创建被 Git 忽略的 `.env.local` 覆盖连接；不要覆盖已有凭据文件：

```dotenv
MEMORY_LLM_BASE_URL=https://open.bigmodel.cn/api/coding/paas/v4
MEMORY_LLM_API_KEY=填写自己的密钥
MEMORY_EMBEDDING_BASE_URL=http://127.0.0.1:11434
LOCAL_PORT=8088
```

```sh
chmod 600 .env.local
make local LOCAL_MODE=enhanced
# 或 make local-debug LOCAL_MODE=enhanced
```

增强模式的实际写入会调用模型并消耗额度。`/health` 不证明远端模型可用；本次改造未重新调用远端模型验证。

配置规则：行为开关来自所选模式的固定配置，覆盖 shell 中同名行为开关；连接信息和本地端口按 shell > `.env.local` > `.env` 优先。`MEMORY_CONFIG_FILE` 是原有容器/实验参数，本地入口通过 `LOCAL_MODE` 明确选择模式，不读取它。默认仅绑定本机。

离线数据默认 `service/.data/local-offline`，增强数据默认 `service/.data/local-enhanced`，与容器卷及历史评测分离。需要新目录时设置 `LOCAL_DATA_DIR=service/.data/my-run`；相对路径按工作区根目录解析。模型审计存入该目录，完整模型正文追踪默认不开启。切换模式不要复用同一数据目录。

换 HTTP 端口需让客户端同步：

```sh
LOCAL_PORT=8099 make local
make contract BASE_URL=http://127.0.0.1:8099
```

## 测试及评测

`make test` 不依赖 Docker。原有 `make eval-init`、`make eval-locomo`、`make eval-memops` 和 `scripts/run-experiment.py` 可与原生服务配合；正式评测继续使用冻结配置和独立 campaign，不把开发数据混入评测。Answer/Judge 需要各自模型配置和相应 Python 依赖。

只克隆 service 仓库也能使用 `npm ci`、`npm run build`、`npm start`、`npm run dev` 和 `npm run debug`；这些命令读取该仓库自己的 `.env`。工作区入口不依赖 service 的 `.env`，通过环境变量传入固定配置，二者不会交叉覆盖。
