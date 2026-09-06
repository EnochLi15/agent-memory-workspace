# V1 候选部署与交接

当前仍为候选版本，完整评测、长时存活及最终归档验收未完成。历史归档与旧镜像说明见 [历史部署记录](DEPLOYMENT-HISTORICAL.md)，不能用旧报告证明当前版本已完成。

## 配置与启动

增强配置只有一份：`configs/release-enhanced.env`。离线配置为 `configs/release-offline.env`。Compose 从所选文件读取行为开关，只从本地 `.env` 读取连接地址、凭据和部署参数；旧实验开关不会覆盖发布配置。`service/.env.example` 是增强配置的独立服务模板。

宿主机构建需要 Node 24.18.0、Python 3 和 Docker；运行 service 镜像不依赖 eval。当前容器实测平台为 Docker Linux arm64，宿主机 macOS arm64。没有将 amd64 标为已验证。

```sh
nvm use
make init
make build
# 已有 .env 时保留原文件；首次部署才复制：
cp -n .env.example .env
# 在 .env 填写可从容器访问的 MEMORY_LLM_BASE_URL 和 MEMORY_LLM_API_KEY。
make up
make contract
make down
```

默认构建标签为 `comp-agent-memory-service:v1-candidate`，不是正式版本。增强模式默认使用独立卷 `comp-agent-memory-enhanced-v1`，不会接管历史卷。`MEMORY_PORT` 默认8088；`MEMORY_SERVICE_IMAGE` 和 `MEMORY_VOLUME` 可以指定镜像及已有兼容快照。不要将增强与离线配置指向同一卷。

运行无模型的离线模式：

```sh
make up-offline
make contract
make down
```

离线模式使用独立卷 `comp-agent-memory-offline-v1`、规则提取与词法检索。它支持已验证的简单明确事实、更新和遗忘；复杂语义不等价于增强模式。不能安全绑定的操作应返回错误。不要用离线成绩代表增强模式。`make down` 保留数据，不使用 `down -v` 作为正常停止命令。

## 本地 embedding 与模型

增强写入固定使用 gpt-5.5，默认辅助模型为 gpt-5.4-mini；全部模型阶段共用有界预算。embedding 使用宿主机 Ollama 的 `nomic-embed-text:latest`，768维，固定 digest：

`0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f`

先在宿主机安装或导入该模型，再启动增强服务。服务不会在请求中下载模型。Docker Desktop 默认使用 `http://host.docker.internal:11434`；其它环境需设置可达的 `DOCKER_EMBEDDING_BASE_URL`。本地 LLM 也必须使用容器可达地址；容器内的127.0.0.1指向容器自身。已有模型、固定归档导入和相关冷启动限制见 [模型配置](MODEL-SETUP.md) 与历史部署记录。

更换 embedding 模型或来源格式需要新数据目录和重新灌入，不在旧向量空间上混写。模型可用性不由 `/health` 证明：该接口验证进程和存储工作线程，实际模型连通性由完整写入演示验证。

## 可执行闭环演示

`make contract` 是现有独立 HTTP 演示与验收入口：创建独立演示用户，写入经理/门禁码/城市，验证立即检索与幂等，更新城市并验证旧值不再作为当前值，遗忘门禁码并验证经理仍保留，同时检查用户隔离、错误请求、路由白名单和证据条数。

该命令不产生最终问答或判分，也不读取服务数据库。完整写入→检索→回答→判分使用固定小集和独立 eval；存储层遗忘另有只读检查。不要把 HTTP 演示当作全量准确率。

## 日志与故障定位

```sh
docker compose logs --tail 100 memory-service
```

普通 HTTP 日志含 `id`、`request_id`（写入）、哈希租户标识、阶段、模式、状态、耗时和错误码；不记录消息正文、查询、请求头和凭据。健康轮询成功不反复刷日志。Docker日志按10MB、3份轮转。

模型阶段、传输错误、用量与恢复记录位于卷内 `/data/model-audit.jsonl`。仅在明确诊断时开启私有 `MEMORY_MODEL_TRACE`，其中会含模型输入输出，不能放进常规交付包。业务日志不能替代物理磁盘擦除证明。

| 错误 | 处理边界 |
| --- | --- |
| WRITE_CONTINUATION_PENDING | 同进程内使用完全相同ID和载荷有界重试，最多3次；不换ID补成功 |
| EXTRACTION_UNAVAILABLE / VERIFICATION_UNAVAILABLE | 检查模型审计中的连接、超时、输出协议原因；不将失败写入当作完成 |
| EVIDENCE_VALIDATION / OPERATION_* | 来源、语义或操作目标未通过；检查隔离诊断，不能放宽遗忘及邻居检查 |
| SOURCE_FORMAT | 当前配置与数据不兼容；恢复匹配配置/镜像/快照，或使用新卷重新灌入 |
| STORAGE / WORKER_EXIT | 检查卷权限、空间和工作线程；失败事务不能作为已提交记录 |
| DEADLINE | 请求超过截止时间；通过原请求身份核对回执，不盲目换ID |

服务进程重启会失去未提交准备的内存模型响应缓存；这类请求明确终止，不宣称支持跨进程继续。已提交请求的回执和记忆会持久保留。未知数据版本及无版本非空数据在打开租户数据库时拒绝；不会自动迁移或假装兼容。

## 备份、恢复和升级

先正常停止服务，再备份完整数据目录，包括尚未检查点落盘的WAL文件。不要只复制某个正在写入的 `memory.sqlite`。下面以默认增强卷为例，备份目录保存在宿主机；同时记录镜像ID、配置和备份SHA256。

```sh
mkdir -p backups
backup_name="memory-$(date +%Y%m%d-%H%M%S).tar.gz"
docker compose stop
docker run --rm --network none --user 0:0 --entrypoint tar \
  -v comp-agent-memory-enhanced-v1:/data:ro -v "$PWD/backups:/backup" \
  comp-agent-memory-service:v1-candidate -czf "/backup/$backup_name" -C /data .
shasum -a 256 "backups/$backup_name"
docker compose start
```

恢复到新卷，保留原卷作为回退点。为避免覆盖已有数据，先确认目标卷不存在；不要将未检查的归档解压到在线卷。

```sh
restore_volume="memory-restored-$(date +%Y%m%d-%H%M%S)"
docker volume create "$restore_volume"
docker run --rm --network none --user 0:0 --entrypoint tar \
  -v "$restore_volume:/data" -v "$PWD/backups:/backup:ro" \
  comp-agent-memory-service:v1-candidate -xzf "/backup/$backup_name" -C /data .
MEMORY_VOLUME_EXTERNAL=true MEMORY_VOLUME="$restore_volume" docker compose up -d --wait
```

使用与快照相匹配的配置及镜像。离线恢复同时指定 `MEMORY_CONFIG_FILE=configs/release-offline.env`。检查原用户的当前状态、被遗忘值和旧请求回执，再切换正式流量。`make contract` 会创建新演示用户，不能单独证明旧用户恢复正确。

升级前保留旧镜像ID、配置和停机快照；先用新镜像在快照副本上验证，再更换部署。回退时同时恢复旧镜像与兼容快照，不能只换镜像后让旧代码读取未知的新格式。当前版本没有通用自动迁移器，格式变更需要新卷重新灌入。已执行的两种模式备份读回证据分别见 [离线](../reports/v1-offline-backup-restore.json) 和 [增强](../reports/v1-enhanced-backup-restore.json)。

## 长评测运行

复用 `scripts/run-experiment.py --detach` 启动独立进程会话，`--status` 查询真实进程身份和退出状态。原结果不覆盖、失活记录不当作运行中，未知退出原因保留unknown。macOS的空闲睡眠防护不能保证阻止合盖或强制睡眠。完整评测应放在保持唤醒的宿主机运行。

[干净递归克隆验证](../reports/v1-clean-clone-acceptance.json) 已通过构建、353项服务测试、24项Node评测器测试、17项Python测试、增强HTTP闭环及重启读回；临时凭据副本已移除。[兼容镜像回退](../reports/v1-compatible-rollback.json) 已通过。固定小集和完整评测的证据仍待完成。尚未完成的项目继续按 [V1交付计划](29-V1交付收敛计划.md) 验收，不能把本文候选命令等同于正式发布完成。
